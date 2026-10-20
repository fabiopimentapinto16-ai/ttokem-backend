"""Ttoken Studio — backend.

Fluxo: ideia -> Ttokem (comprime só se compensar) -> maestro de IAs (roteiro,
legenda, hashtags, prompt de vídeo) -> maestro de vídeo -> resultado.
O usuário sempre dá o último clique para postar (o backend nunca posta).
"""
import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import ttokem
from maestro import AllProvidersFailed, Maestro
from video import VideoFailed, VideoMaestro

app = FastAPI(title="Ttoken Studio")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

llm = Maestro()
videos = VideoMaestro()
pool = ThreadPoolExecutor(max_workers=4)
JOBS, JOBS_LOCK = {}, threading.Lock()
JOB_TTL = 3600

SYSTEM = (
    "Você cria conteúdo para vídeos curtos de redes sociais. Responda SOMENTE "
    "com JSON válido, sem texto fora dele, com as chaves: "
    '"titulo" (curto), "roteiro" (3-5 cenas curtas), "legenda" (pronta para '
    'postar, em português do Brasil, com gancho e chamada para ação), '
    '"hashtags" (lista de 5 a 8 strings com #), "prompt_video" (descrição '
    "visual da cena em INGLÊS, até 350 caracteres, sem texto na tela).")


class GerarReq(BaseModel):
    ideia: str = Field(min_length=3, max_length=4000)
    plataforma: str = "instagram"


class ComprimirReq(BaseModel):
    texto: str
    modo: str = "auto"      # auto | light | v24


def _parse_json(text: str):
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    try:
        return json.loads(m.group(0) if m else t)
    except (ValueError, AttributeError):
        return None


def _set(job_id, **kw):
    with JOBS_LOCK:
        JOBS[job_id].update(kw, atualizado=time.time())


def _cleanup():
    cut = time.time() - JOB_TTL
    with JOBS_LOCK:
        for k in [k for k, v in JOBS.items() if v["atualizado"] < cut]:
            del JOBS[k]


def _run(job_id, req: GerarReq):
    try:
        # 1) Ttokem: só comprime se realmente economiza (ideias curtas passam direto)
        comp = ttokem.compress(req.ideia, "auto")
        _set(job_id, etapa="IA escrevendo roteiro e legenda",
             tokens={"antes": comp["original_tokens"],
                     "depois": comp["compressed_tokens"],
                     "economia_pct": comp["saving_pct"], "modo": comp["mode"]})

        # 2) Maestro de IAs (troca sozinho se um crédito acabar)
        try:
            raw, api_llm = llm.chat([
                {"role": "system", "content": SYSTEM},
                {"role": "user",
                 "content": f"Plataforma: {req.plataforma}\nIdeia: {comp['text']}"}])
        except AllProvidersFailed as e:
            _set(job_id, status="erro", erro=f"Nenhuma IA disponível: {e}")
            return
        data = _parse_json(raw) or {"legenda": raw, "prompt_video": req.ideia}
        tags = data.get("hashtags") or []
        if isinstance(tags, str):
            tags = tags.split()
        legenda = str(data.get("legenda", "")).strip()
        texto = (legenda + ("\n\n" + " ".join(tags) if tags else "")).strip()
        _set(job_id, api_llm=api_llm, titulo=data.get("titulo", ""),
             roteiro=data.get("roteiro", ""), texto=texto,
             etapa="Gerando vídeo")

        # 3) Maestro de vídeo (prompt em linguagem natural, NUNCA o codebook)
        prompt_video = str(data.get("prompt_video") or req.ideia)[:500]
        try:
            url, api_video = videos.generate(prompt_video)
            _set(job_id, status="pronto", video_url=url, api_video=api_video,
                 etapa="Pronto")
        except VideoFailed as e:
            # a legenda continua válida mesmo sem vídeo
            _set(job_id, status="pronto", video_url="", api_video="nenhum",
                 aviso=f"Vídeo indisponível agora: {e}", etapa="Pronto (sem vídeo)")
    except Exception as e:      # nunca deixar o job preso em "processando"
        _set(job_id, status="erro", erro=f"Falha inesperada: {e}")


@app.get("/health")
def health():
    return {"ok": True, "ias": llm.status(), "videos": videos.status()}


@app.post("/api/comprimir")
def comprimir(req: ComprimirReq):
    if req.modo not in ("auto", "light", "v24"):
        raise HTTPException(400, "modo deve ser auto, light ou v24")
    return ttokem.compress(req.texto, req.modo)


@app.post("/api/gerar")
def gerar(req: GerarReq):
    _cleanup()
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "processando", "etapa": "Iniciando",
                        "criado": time.time(), "atualizado": time.time()}
    pool.submit(_run, job_id, req)
    return {"success": True, "job_id": job_id}


@app.get("/api/status/{job_id}")
def status(job_id: str):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "job não encontrado (expirou?)")
        out = dict(job)
    out["success"] = out["status"] != "erro"
    return out
