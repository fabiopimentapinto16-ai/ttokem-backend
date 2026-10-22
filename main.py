"""Ttoken Studio — backend.

Fluxo: ideia -> Ttokem (comprime só se compensar) -> maestro de IAs (roteiro,
legenda, hashtags, prompt de vídeo) -> maestro de vídeo -> resultado.
O usuário sempre dá o último clique para postar (o backend nunca posta).
"""
import os
import sys
import threading
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import pipeline
import ttokem
import video as video_mod
from maestro import Maestro
from pipeline import LLM_CACHE, VIDEO_CACHE   # noqa: F401  (mesmos objetos)
from video import VideoMaestro

app = FastAPI(title="Ttoken Studio")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

llm = Maestro()
videos = VideoMaestro()
pool = ThreadPoolExecutor(max_workers=4)
JOBS, JOBS_LOCK = {}, threading.Lock()
JOB_TTL = 3600
RATE, RATE_LOCK = defaultdict(deque), threading.Lock()
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_HOUR", "20"))   # pedidos por IP/hora
PUBLIC_URL = os.environ.get("PUBLIC_URL",
                            "https://ttokem-backend-8ba2a.containers.snapdeploy.app").rstrip("/")

class GerarReq(BaseModel):
    ideia: str = Field(min_length=3, max_length=4000)
    plataforma: str = "instagram"
    regenerar: bool = False        # True ignora o cache e gera de novo


class ComprimirReq(BaseModel):
    texto: str
    modo: str = "auto"      # auto | light | v24


def _set(job_id, **kw):
    with JOBS_LOCK:
        JOBS[job_id].update(kw, atualizado=time.time())


def _cleanup():
    cut = time.time() - JOB_TTL
    with JOBS_LOCK:
        for k in [k for k, v in JOBS.items() if v["atualizado"] < cut]:
            del JOBS[k]


def _run(job_id, req: GerarReq):
    def report(**kw):
        if kw.get("video_url"):
            with JOBS_LOCK:
                if "_dl" in JOBS[job_id]:   # arquivo exige token: o backend repassa o download
                    kw["video_url"] = f"{PUBLIC_URL}/api/video/{job_id}"
        _set(job_id, **kw)

    pipeline.produzir(req.ideia, req.plataforma, llm, videos, report, req.regenerar)


@app.get("/")
def raiz():
    return {"ok": True, "app": "Ttoken Studio"}


VERSAO = "1.3.0"


def _resumo():
    return {"texto": sorted({x["slot"].split("#")[0] for x in llm.status()}),
            "video": sorted({x["tipo"] for x in videos.status()})}


@app.get("/health")
def health():
    """Diagnóstico: mostra QUAIS provedores estão ativos (nunca as chaves)."""
    return {"ok": True, "versao": VERSAO, "python": sys.version.split()[0],
            "ativos": _resumo(), "ias": llm.status(), "videos": videos.status()}


class ChatReq(BaseModel):
    mensagem: str = Field(min_length=1, max_length=4000)
    historico: list = []


@app.post("/api/chat")
def chat(req: ChatReq, request: Request):
    """Conversa livre / pedido de código com o maestro de IAs."""
    if not _limite(request):
        raise HTTPException(429, "Limite de pedidos por hora atingido.")
    msgs = [{"role": "system", "content":
             "Você é o assistente do Ttoken Studio: roteiros, legendas, ideias de vídeo e "
             "código (Python/Kivy). Responda em português, claro e prático."}]
    for h in (req.historico or [])[-10:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:4000]})
    msgs.append({"role": "user", "content": req.mensagem.strip()})
    try:
        texto, api = llm.chat(msgs, max_tokens=1500)
    except Exception as e:
        raise HTTPException(502, f"IA indisponível: {e}")
    return {"ok": True, "resposta": texto, "ia": api}


class ColabReq(BaseModel):
    url: str
    secret: str


@app.post("/api/colab/registrar")
def colab_registrar(req: ColabReq):
    """O notebook do Colab avisa aqui o link do túnel (muda a cada sessão)."""
    esperado = os.environ.get("COLAB_SECRET", "")
    if not esperado or req.secret != esperado:
        raise HTTPException(403, "COLAB_SECRET ausente ou incorreto")
    if not req.url.startswith("https://"):
        raise HTTPException(400, "url inválida")
    video_mod.colab_registrar(req.url)
    return {"ok": True}


@app.post("/api/comprimir")
def comprimir(req: ComprimirReq):
    if req.modo not in ("auto", "light", "v24"):
        raise HTTPException(400, "modo deve ser auto, light ou v24")
    return ttokem.compress(req.texto, req.modo)


def _limite(request) -> bool:
    """True se o IP ainda pode gerar. Protege seus créditos grátis contra abuso."""
    ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or getattr(getattr(request, "client", None), "host", "?"))
    now = time.time()
    with RATE_LOCK:
        q = RATE[ip]
        while q and q[0] < now - 3600:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return False
        q.append(now)
        return True


@app.post("/api/gerar")
def gerar(req: GerarReq, request: Request):
    if not _limite(request):
        raise HTTPException(429, "Limite de pedidos por hora atingido. Tente mais tarde.")
    _cleanup()
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "processando", "etapa": "Iniciando",
                        "passo": 0, "criado": time.time(),
                        "atualizado": time.time()}
    pool.submit(_run, job_id, req)
    return {"success": True, "job_id": job_id}


@app.get("/api/status/{job_id}")
def status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "job não encontrado (expirou?)")
        out = {k: v for k, v in job.items() if not k.startswith("_")}
    out["success"] = out["status"] != "erro"
    return out


@app.get("/api/video/{job_id}")
def baixar_video(job_id: str):
    """Repassa o vídeo quando o gerador exige token no download."""
    import requests
    from fastapi.responses import StreamingResponse
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        src, hdr = (job or {}).get("_src"), (job or {}).get("_dl")
    if not src:
        raise HTTPException(404, "vídeo não encontrado")
    up = requests.get(src, headers=hdr, stream=True, timeout=60)
    if up.status_code != 200:
        raise HTTPException(502, f"origem respondeu {up.status_code}")
    return StreamingResponse(up.iter_content(65536),
                             media_type=up.headers.get("content-type", "video/mp4"))


print("[ttoken] v%s | python %s | provedores ativos: %s" % (
    VERSAO, sys.version.split()[0], _resumo()), flush=True)

if __name__ == "__main__":      # python main.py  (alguns hosts iniciam assim)
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
