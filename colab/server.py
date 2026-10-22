"""Servidor de vídeo para o Google Colab (GPU T4 grátis). Só biblioteca padrão.

Fluxo: o backend (SnapDeploy) pede POST /generate -> consulta /status/{id} ->
baixa /video/{id}. Segurança: tudo exige o cabeçalho X-Token == COLAB_SECRET.
O túnel público (cloudflared) muda a cada sessão; por isso este script avisa o
backend (POST /api/colab/registrar) a cada 60 s.

Variáveis: COLAB_SECRET (obrigatória), BACKEND_URL, COLAB_MODEL (cogvideox-2b |
ltx), PORT (8765), TTOKEN_FAKE=1 (teste sem GPU), NO_TUNNEL=1.
"""
import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ.get("COLAB_SECRET", "")
BACKEND = os.environ.get("BACKEND_URL", "").rstrip("/")
PORT = int(os.environ.get("PORT", "8765"))
OUT = tempfile.mkdtemp(prefix="ttoken_")
JOBS, LOCK, FILA = {}, threading.Lock(), queue.Queue()
_PIPE = {}


def _gerar_real(prompt, destino):
    import torch
    from diffusers.utils import export_to_video
    if "pipe" not in _PIPE:
        if os.environ.get("COLAB_MODEL", "cogvideox-2b") == "ltx":
            from diffusers import LTXPipeline
            pipe = LTXPipeline.from_pretrained("Lightricks/LTX-Video", torch_dtype=torch.bfloat16)
        else:   # CogVideoX-2b foi feito para fp16: roda na T4 (16 GB) com offload
            from diffusers import CogVideoXPipeline
            pipe = CogVideoXPipeline.from_pretrained("THUDM/CogVideoX-2b", torch_dtype=torch.float16)
            pipe.vae.enable_tiling()
        pipe.enable_model_cpu_offload()
        _PIPE["pipe"] = pipe
    pipe = _PIPE["pipe"]
    if os.environ.get("COLAB_MODEL", "cogvideox-2b") == "ltx":
        frames = pipe(prompt=prompt, width=512, height=768, num_frames=97,
                      num_inference_steps=40).frames[0]
        fps = 24
    else:
        frames = pipe(prompt=prompt, num_frames=49, num_inference_steps=40,
                      guidance_scale=6).frames[0]
        fps = 8
    bruto = destino + ".raw.mp4"
    export_to_video(frames, bruto, fps=fps)
    _vertical(bruto, destino)


def _vertical(origem, destino):
    """Recorta o centro para 9:16 (Reels/TikTok/Shorts). Se falhar, mantém o original."""
    try:
        import imageio_ffmpeg
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run([ff, "-y", "-i", origem, "-vf",
                        "crop='min(iw,ih*9/16)':ih,scale=540:960:flags=lanczos",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", destino],
                       check=True, capture_output=True, timeout=300)
    except Exception:
        os.replace(origem, destino)


def _gerar_fake(prompt, destino):
    time.sleep(0.2)
    with open(destino, "wb") as f:
        f.write(b"FAKEMP4:" + prompt.encode())


def _worker():
    gerar = _gerar_fake if os.environ.get("TTOKEN_FAKE") else _gerar_real
    while True:
        job_id = FILA.get()
        with LOCK:
            JOBS[job_id]["status"] = "running"
        try:
            arq = os.path.join(OUT, job_id + ".mp4")
            gerar(JOBS[job_id]["prompt"], arq)
            with LOCK:
                JOBS[job_id].update(status="done", file=arq)
        except Exception as e:      # noqa: BLE001 — o job nunca fica preso
            with LOCK:
                JOBS[job_id].update(status="error", error=str(e)[:300])


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _ok(self):
        if not SECRET or self.headers.get("X-Token", "") != SECRET:
            self._json(401, {"error": "token inválido"})
            return False
        return True

    def do_GET(self):
        if self.path == "/":
            return self._json(200, {"ok": True, "app": "ttoken-colab"})
        if not self._ok():
            return
        m = re.fullmatch(r"/(status|video)/([0-9a-f]+)", self.path)
        job = JOBS.get(m.group(2)) if m else None
        if not job:
            return self._json(404, {"error": "job não encontrado"})
        if m.group(1) == "status":
            return self._json(200, {k: v for k, v in job.items() if k in ("status", "error")})
        if job["status"] != "done":
            return self._json(409, {"error": "ainda não pronto"})
        size = os.path.getsize(job["file"])
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(job["file"], "rb") as f:
            while chunk := f.read(65536):
                self.wfile.write(chunk)

    def do_POST(self):
        if self.path != "/generate" or not self._ok():
            return self._json(404, {"error": "?"}) if self.path != "/generate" else None
        try:
            n = int(self.headers.get("Content-Length", "0"))
            prompt = str(json.loads(self.rfile.read(n))["prompt"]).strip()[:500]
        except Exception:
            return self._json(400, {"error": "envie {\"prompt\": \"...\"}"})
        if not prompt:
            return self._json(400, {"error": "prompt vazio"})
        job_id = uuid.uuid4().hex[:12]
        with LOCK:
            JOBS[job_id] = {"status": "queued", "prompt": prompt}
        FILA.put(job_id)
        self._json(200, {"job_id": job_id})


def _tunel():
    """Abre o túnel cloudflared e devolve a URL https pública."""
    exe = os.environ.get("CLOUDFLARED", "./cloudflared")
    p = subprocess.Popen([exe, "tunnel", "--url", f"http://127.0.0.1:{PORT}", "--no-autoupdate"],
                         stderr=subprocess.PIPE, text=True)
    for linha in p.stderr:
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", linha)
        if m:
            threading.Thread(target=lambda: [None for _ in p.stderr], daemon=True).start()
            return m.group(0)
    raise RuntimeError("cloudflared não devolveu URL")


def _avisar(url):
    """Avisa o backend a cada 60 s: 'estou online neste link'."""
    corpo = json.dumps({"url": url, "secret": SECRET}).encode()
    while True:
        try:
            req = urllib.request.Request(BACKEND + "/api/colab/registrar", data=corpo,
                                         headers={"Content-Type": "application/json",
                                                  "User-Agent": "ttoken-colab"})
            urllib.request.urlopen(req, timeout=20).read()
            print("registrado no backend:", url, flush=True)
        except Exception as e:      # noqa: BLE001
            print("aviso ao backend falhou:", e, flush=True)
        time.sleep(60)


def main():
    if not SECRET:
        sys.exit("Defina COLAB_SECRET (o mesmo valor que está no SnapDeploy).")
    threading.Thread(target=_worker, daemon=True).start()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("servidor local na porta", PORT, flush=True)
    if os.environ.get("NO_TUNNEL"):
        threading.Event().wait()
    url = _tunel()
    print("link público:", url, flush=True)
    if BACKEND:
        _avisar(url)            # fica aqui para sempre (mantém a célula viva)
    else:
        threading.Event().wait()


if __name__ == "__main__":
    main()
