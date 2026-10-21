"""Maestro de vídeo: tenta cada gerador na ordem; se um falhar ou ficar sem
crédito, passa ao próximo.

Geradores suportados (ordem padrão: mpt, proxy, magichour, fal, replicate —
os gratuitos/self-hosted primeiro; mude com VIDEO_ORDER):
  proxy     endpoints "job assíncrono" estilo OpenAI (o seu proxy atual)
            VIDEO_PROVIDERS="base1|modelo|chave, base2" e/ou PROXY_URL (legado)
  fal       fal.ai  — 1 chave, muitos modelos de vídeo
            FAL_KEY, FAL_VIDEO_MODEL, FAL_EXTRA_INPUT (JSON opcional)
  replicate Replicate — 1 token, muitos modelos de vídeo
            REPLICATE_API_TOKEN, REPLICATE_VIDEO_MODEL, REPLICATE_EXTRA_INPUT
  magichour Magic Hour — plano grátis com créditos (modelo "default" = ltx-2.5)
            MAGIC_HOUR_API_KEY, MAGIC_HOUR_MODEL, MAGIC_HOUR_SECONDS,
            MAGIC_HOUR_EXTRA_INPUT
  mpt       MoneyPrinterTurbo self-hosted (precisa de servidor próprio)
            MPT_URL, MPT_BASIC_AUTH ("usuario:senha", opcional)
            (adaptador escrito pela API publicada; ainda sem servidor real testado)
generate() devolve (url, gerador, headers_de_download | None).
"""
import json
import os
import threading
import time

import requests


class VideoFailed(Exception):
    pass


class NoCredit(VideoFailed):
    """Crédito/limite do gerador acabou: esfria por mais tempo."""


FAL_DEFAULT_MODEL = "fal-ai/bytedance/seedance/v1.5/pro/text-to-video"
REPLICATE_DEFAULT_MODEL = "minimax/video-01"


def _extra(var, default=None):
    try:
        return json.loads(os.environ.get(var, "")) if os.environ.get(var) else (default or {})
    except ValueError:
        return default or {}


def _providers():
    out = {"mpt": [], "proxy": [], "magichour": [], "fal": [], "replicate": []}
    default_model = os.environ.get("VIDEO_MODEL", "wan-fast")
    entries = [e.strip() for e in os.environ.get("VIDEO_PROVIDERS", "").split(",")
               if e.strip()]
    legacy = os.environ.get("PROXY_URL", "").strip()
    if legacy and not any(e.split("|")[0] == legacy for e in entries):
        entries.append(legacy)
    for e in entries:
        parts = [p.strip() for p in e.split("|")]
        base = parts[0].rstrip("/")
        out["proxy"].append({
            "id": base, "kind": "proxy", "base": base,
            "model": parts[1] if len(parts) > 1 and parts[1] else default_model,
            "key": parts[2] if len(parts) > 2 else ""})
    if os.environ.get("MPT_URL"):
        base = os.environ["MPT_URL"].rstrip("/")
        out["mpt"].append({"id": f"mpt:{base}", "kind": "mpt", "base": base,
                           "model": "moneyprinterturbo",
                           "key": os.environ.get("MPT_BASIC_AUTH", "")})
    if os.environ.get("MAGIC_HOUR_API_KEY"):
        m = os.environ.get("MAGIC_HOUR_MODEL", "default")
        out["magichour"].append({"id": f"magichour:{m}", "kind": "magichour",
                                 "model": m, "key": os.environ["MAGIC_HOUR_API_KEY"]})
    if os.environ.get("FAL_KEY"):
        m = os.environ.get("FAL_VIDEO_MODEL", FAL_DEFAULT_MODEL)
        out["fal"].append({"id": f"fal:{m}", "kind": "fal", "model": m,
                           "key": os.environ["FAL_KEY"]})
    if os.environ.get("REPLICATE_API_TOKEN"):
        m = os.environ.get("REPLICATE_VIDEO_MODEL", REPLICATE_DEFAULT_MODEL)
        out["replicate"].append({"id": f"replicate:{m}", "kind": "replicate",
                                 "model": m, "key": os.environ["REPLICATE_API_TOKEN"]})
    order = [n.strip() for n in os.environ.get("VIDEO_ORDER", "mpt,proxy,magichour,fal,replicate").split(",")]
    order += [n for n in out if n not in order]
    return [p for n in order for p in out.get(n, [])]


class VideoMaestro:
    def __init__(self, poll_every=5, max_wait=240):
        self.poll_every, self.max_wait = poll_every, max_wait
        self._cool, self._lock = {}, threading.Lock()

    def status(self):
        now = time.time()
        return [{"gerador": p["id"], "tipo": p["kind"],
                 "disponivel": self._cool.get(p["id"], 0) <= now}
                for p in _providers()]

    def _penalize(self, pid, seconds):
        with self._lock:
            self._cool[pid] = time.time() + seconds

    # ----------------------------------------------------- adaptadores
    def _wait(self, check):
        """check() -> (done, resultado). Consulta até acabar o prazo."""
        deadline = time.time() + self.max_wait
        while time.time() < deadline:
            done, res = check()
            if done:
                return res
            time.sleep(self.poll_every)
        raise VideoFailed("tempo esgotado")

    @staticmethod
    def _guard(r):
        if r.status_code in (401, 402, 403, 429):
            raise NoCredit(f"HTTP {r.status_code}")
        r.raise_for_status()

    def _proxy(self, p, prompt):
        hdr = {"Authorization": f"Bearer {p['key']}"} if p["key"] else {}
        r = requests.post(f"{p['base']}/videos", headers=hdr, timeout=30,
                          json={"model": p["model"], "prompt": prompt})
        self._guard(r)
        job = r.json()["id"]

        def check():
            st = requests.get(f"{p['base']}/videos/{job}", headers=hdr, timeout=20).json()
            if st.get("status") == "failed":
                raise VideoFailed(st.get("error") or "falhou")
            return st.get("status") == "completed", None
        self._wait(check)
        return f"{p['base']}/videos/{job}/content", (hdr or None)

    def _magichour(self, p, prompt):
        hdr = {"Authorization": f"Bearer {p['key']}", "Content-Type": "application/json"}
        body = {"name": "ttoken", "model": p["model"], "aspect_ratio": "9:16",
                "end_seconds": int(os.environ.get("MAGIC_HOUR_SECONDS", "5")),
                "style": {"prompt": prompt}}
        body.update(_extra("MAGIC_HOUR_EXTRA_INPUT"))
        url = "https://api.magichour.ai/v1/text-to-video"
        r = requests.post(url, headers=hdr, json=body, timeout=30)
        if r.status_code in (400, 422) and "aspect_ratio" in body:
            body.pop("aspect_ratio")        # nem todo modelo aceita 9:16
            r = requests.post(url, headers=hdr, json=body, timeout=30)
        self._guard(r)
        vid = r.json()["id"]
        box = {}

        def check():
            st = requests.get(f"https://api.magichour.ai/v1/video-projects/{vid}",
                              headers=hdr, timeout=20).json()
            if st.get("status") == "complete":
                box["url"] = next((d["url"] for d in st.get("downloads", []) if d.get("url")), None)
                return True, None
            if st.get("status") in ("error", "canceled"):
                raise VideoFailed(str((st.get("error") or {}).get("message") or st["status"])[:200])
            return False, None
        self._wait(check)
        if not box.get("url"):
            raise VideoFailed("resposta sem URL de vídeo")
        return box["url"], None

    def _mpt(self, p, prompt):
        auth = tuple(p["key"].split(":", 1)) if ":" in p["key"] else None
        r = requests.post(f"{p['base']}/api/v1/videos", auth=auth, timeout=30,
                          json={"video_subject": prompt, "video_aspect": "9:16",
                                "video_source": "pexels"})
        self._guard(r)
        j = r.json()
        task = (j.get("data") or j).get("task_id")
        if not task:
            raise VideoFailed("sem task_id")
        box = {}

        def check():
            j = requests.get(f"{p['base']}/api/v1/tasks/{task}", auth=auth, timeout=20).json()
            d = j.get("data") or j
            if d.get("state") == -1:
                raise VideoFailed("falha no MoneyPrinterTurbo")
            if d.get("state") == 1:
                box["url"] = next(iter(d.get("videos") or []), None)
                return True, None
            return False, None
        self._wait(check)
        if not box.get("url"):
            raise VideoFailed("resposta sem URL de vídeo")
        return box["url"], None

    def _fal(self, p, prompt):
        hdr = {"Authorization": f"Key {p['key']}", "Content-Type": "application/json"}
        body = {"prompt": prompt, "aspect_ratio": "9:16"}
        if "seedance" in p["model"]:
            body.update(duration="5", resolution="480p")
        body.update(_extra("FAL_EXTRA_INPUT"))
        r = requests.post(f"https://queue.fal.run/{p['model']}", headers=hdr,
                          json=body, timeout=30)
        self._guard(r)
        sub = r.json()

        def check():
            st = requests.get(sub["status_url"], headers=hdr, timeout=20).json()
            if st.get("status") == "COMPLETED":
                if st.get("error"):
                    raise VideoFailed(str(st["error"])[:200])
                return True, None
            return False, None
        self._wait(check)
        res = requests.get(sub["response_url"], headers=hdr, timeout=30).json()
        url = (res.get("video") or {}).get("url") or \
              next((v.get("url") for v in res.get("videos", []) if v.get("url")), None)
        if not url:
            raise VideoFailed("resposta sem URL de vídeo")
        return url, None

    def _replicate(self, p, prompt):
        hdr = {"Authorization": f"Bearer {p['key']}", "Content-Type": "application/json",
               "Prefer": "wait=30"}
        inp = {"prompt": prompt}
        inp.update(_extra("REPLICATE_EXTRA_INPUT"))
        r = requests.post(f"https://api.replicate.com/v1/models/{p['model']}/predictions",
                          headers=hdr, json={"input": inp}, timeout=60)
        self._guard(r)
        pred = r.json()

        def check():
            nonlocal pred
            st = pred.get("status")
            if st in ("failed", "canceled"):
                raise VideoFailed(str(pred.get("error") or st)[:200])
            if st == "succeeded":
                return True, None
            pred = requests.get(pred["urls"]["get"], headers=hdr, timeout=20).json()
            return pred.get("status") == "succeeded", None
        self._wait(check)
        out = pred.get("output")
        if isinstance(out, dict):
            out = next((v for v in out.values() if isinstance(v, str) and v.startswith("http")), None)
        if isinstance(out, list):
            out = out[0] if out else None
        if not isinstance(out, str) or not out.startswith("http"):
            raise VideoFailed("resposta sem URL de vídeo")
        # arquivos servidos por api.replicate.com exigem o token no download
        needs_auth = "api.replicate.com" in out
        return out, ({"Authorization": f"Bearer {p['key']}"} if needs_auth else None)

    # ------------------------------------------------------------ geral
    def generate(self, prompt: str):
        """Retorna (video_url, gerador, headers_de_download|None)."""
        provs = _providers()
        if not provs:
            raise VideoFailed("Nenhum gerador de vídeo configurado "
                              "(FAL_KEY, REPLICATE_API_TOKEN ou VIDEO_PROVIDERS)")
        errors = []
        for p in provs:
            if self._cool.get(p["id"], 0) > time.time():
                errors.append(f"{p['id']}: em cooldown")
                continue
            try:
                url, dl_headers = getattr(self, "_" + p["kind"])(p, prompt)
                return url, p["id"], dl_headers
            except NoCredit as e:
                self._penalize(p["id"], 3600)
                errors.append(f"{p['id']}: sem crédito/chave ({e})")
            except (requests.RequestException, KeyError, ValueError, VideoFailed) as e:
                self._penalize(p["id"], 120)
                errors.append(f"{p['id']}: {e}")
        raise VideoFailed("; ".join(errors))
