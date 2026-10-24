"""Ttoken Studio — backend.

Fluxo: ideia -> Ttokem (comprime só se compensar) -> maestro de IAs (roteiro,
legenda, hashtags, prompt de vídeo) -> maestro de vídeo -> resultado.
O usuário sempre dá o último clique para postar (o backend nunca posta).
"""
import base64
import hmac
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

import audio
import gitops
import pipeline
import trending
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
IMGS = {}                      # id -> (bytes, mime, expira): foto do produto, 1 h
JOB_TTL = 3600
RATE, RATE_LOCK = defaultdict(deque), threading.Lock()
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_HOUR", "20"))   # pedidos por IP/hora
PUBLIC_URL = os.environ.get("PUBLIC_URL",
                            "https://ttokem-backend-8ba2a.containers.snapdeploy.app").rstrip("/")

class GerarReq(BaseModel):
    ideia: str = Field(min_length=3, max_length=4000)
    plataforma: str = "instagram"
    regenerar: bool = False        # True ignora o cache e gera de novo
    tipo: str = "normal"           # "anuncio" = comercial de produto
    imagem_b64: str = Field(default="", max_length=6_000_000)   # foto do produto (opcional)
    sem_video: bool = False        # usuário já tem o vídeo: só gerar legenda
    idioma: str = Field(default="Português do Brasil", max_length=60)   # qualquer idioma
    economia: str = "auto"         # auto (segura) | agressivo (telegráfico, economiza mais)


class ComprimirReq(BaseModel):
    texto: str
    modo: str = "auto"      # auto | light | v24 | agressivo
    tipo: str = ""          # imagem | video | codigo (vazio = detectar)


def _set(job_id, **kw):
    with JOBS_LOCK:
        JOBS[job_id].update(kw, atualizado=time.time())


def _guardar_imagem(b64: str):
    """Valida a foto e devolve a URL pública temporária (id aleatório, expira em 1 h)."""
    try:
        dados = base64.b64decode(b64, validate=False)
    except ValueError:
        raise HTTPException(400, "foto inválida")
    if len(dados) > 4_200_000:
        raise HTTPException(413, "foto grande demais (máx. ~4 MB)")
    mime = ("image/jpeg" if dados[:3] == b"\xff\xd8\xff" else
            "image/png" if dados[:8] == b"\x89PNG\r\n\x1a\n" else
            "image/webp" if dados[:4] == b"RIFF" and dados[8:12] == b"WEBP" else "")
    if not mime:
        raise HTTPException(400, "use foto JPG, PNG ou WEBP")
    img_id = uuid.uuid4().hex
    IMGS[img_id] = (dados, mime, time.time() + 3600)
    return f"{PUBLIC_URL}/api/img/{img_id}"


def _cleanup():
    for k in [k for k, v in IMGS.items() if v[2] < time.time()]:
        IMGS.pop(k, None)
    cut = time.time() - JOB_TTL
    with JOBS_LOCK:
        for k in [k for k, v in JOBS.items() if v["atualizado"] < cut]:
            del JOBS[k]


def _run(job_id, req: GerarReq, imagem_url=""):
    def report(**kw):
        if kw.get("video_url"):
            with JOBS_LOCK:
                if "_dl" in JOBS[job_id]:   # arquivo exige token: o backend repassa o download
                    kw["video_url"] = f"{PUBLIC_URL}/api/video/{job_id}"
        _set(job_id, **kw)

    pipeline.produzir(req.ideia, req.plataforma, llm, videos, report, req.regenerar,
                      tipo=req.tipo, imagem_url=imagem_url, sem_video=req.sem_video,
                      idioma=req.idioma, modo_ttokem=req.economia)


@app.get("/")
def raiz():
    return {"ok": True, "app": "Ttoken Studio"}


VERSAO = "1.10.1"


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
    idioma: str = Field(default="", max_length=60)      # idioma da resposta (qualquer um)


@app.post("/api/chat")
def chat(req: ChatReq, request: Request):
    """Conversa livre / pedido de código com o maestro de IAs."""
    if not _limite(request):
        raise HTTPException(429, "Limite de pedidos por hora atingido.")
    msgs = [{"role": "system", "content":
             "Você é o assistente do Ttoken Studio: roteiros, legendas, ideias de vídeo e "
             "código (Python/Kivy). Responda em "
             + (pipeline.limpar_idioma(req.idioma) if req.idioma.strip() else "português")
             + ", claro e prático."}]
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


# ------------------------------------------------ áudio -> texto
class AudioReq(BaseModel):
    b64: str = Field(max_length=40_000_000)
    nome: str = "audio.m4a"


@app.post("/api/transcrever")
def transcrever(req: AudioReq, request: Request):
    if not _limite(request):
        raise HTTPException(429, "Limite de pedidos por hora atingido.")
    try:
        texto = audio.transcrever(base64.b64decode(req.b64), req.nome[:60])
    except audio.SemTranscricao as e:
        raise HTTPException(502, str(e))
    except ValueError:
        raise HTTPException(400, "áudio inválido")
    return {"ok": True, "texto": texto}


# ------------------------------------- compilar pelo Git (protegido por senha)
def _auth_build(request):
    c = gitops.cfg()
    if not c["secret"] or not c["token"]:
        raise HTTPException(503, "Compilar pelo Git não está configurado "
                            "(faltam BUILD_SECRET e GITHUB_TOKEN no servidor).")
    q = getattr(request, "query_params", None) or {}
    dado = request.headers.get("x-build-secret", "") or q.get("s", "")
    if not hmac.compare_digest(dado.encode(), c["secret"].encode()):
        raise HTTPException(403, "senha de build incorreta")
    return c


class ArquivoReq(BaseModel):
    path: str = Field(min_length=1, max_length=120)
    conteudo: str = Field(max_length=400_000)
    mensagem: str = "Código gerado no Ttoken Studio"


def _git(fn, *a, **k):
    try:
        return fn(*a, **k)
    except gitops.GitError as e:
        raise HTTPException(502, str(e))


@app.post("/api/build/arquivo")
def build_arquivo(req: ArquivoReq, request: Request):
    c = _auth_build(request)
    sha = _git(gitops.enviar_arquivo, req.path, req.conteudo, req.mensagem, c)
    return {"ok": True, "commit": sha, "branch": c["branch"]}


@app.post("/api/build/iniciar")
def build_iniciar(request: Request):
    c = _auth_build(request)
    return {"ok": True, "run_id": _git(gitops.iniciar_build, c), "branch": c["branch"]}


@app.get("/api/build/status/{run_id}")
def build_status(run_id: int, request: Request):
    c = _auth_build(request)
    return _git(gitops.status, run_id, c)


@app.get("/api/build/apk/{run_id}")
def build_apk(run_id: int, request: Request):
    from fastapi.responses import Response
    c = _auth_build(request)
    nome, dados = _git(gitops.baixar_apk, run_id, c)
    return Response(dados, media_type="application/vnd.android.package-archive",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@app.get("/api/tendencias")
def tendencias(request: Request, regiao: str = "BR", q: str = ""):
    """Vídeos mais assistidos (API oficial do YouTube) para inspirar ideias originais."""
    if not _limite(request):
        raise HTTPException(429, "Limite de pedidos por hora atingido.")
    try:
        return {"ok": True, "itens": trending.mais_vistos(regiao, q)}
    except trending.SemChaveYoutube as e:
        raise HTTPException(503, str(e))
    except trending.ErroYoutube as e:
        raise HTTPException(502, str(e))


@app.get("/api/testar")
def testar(request: Request):
    """Faz 1 chamada mínima em cada IA configurada e diz qual funciona e qual erro deu
    (nunca mostra chaves). Use para descobrir chave inválida/modelo errado."""
    if not _limite(request):
        raise HTTPException(429, "Limite de pedidos por hora atingido.")
    return {"ok": True, "textos": llm.testar(), "videos": videos.status()}


@app.post("/api/comprimir")
def comprimir(req: ComprimirReq):
    if req.modo not in ("auto", "light", "v24", "agressivo"):
        raise HTTPException(400, "modo deve ser auto, light, v24 ou agressivo")
    return ttokem.compress(req.texto, req.modo, req.tipo or None, presets=True)


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
    imagem_url = _guardar_imagem(req.imagem_b64) if req.imagem_b64 else ""
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "processando", "etapa": "Iniciando",
                        "passo": 0, "criado": time.time(),
                        "atualizado": time.time()}
    pool.submit(_run, job_id, req, imagem_url)
    return {"success": True, "job_id": job_id}


@app.get("/api/img/{img_id}")
def baixar_imagem(img_id: str):
    """Entrega a foto do produto ao gerador de vídeo (link aleatório que expira em 1 h)."""
    item = IMGS.get(img_id)
    if not item or item[2] < time.time():
        raise HTTPException(404, "imagem expirada")
    from fastapi.responses import Response
    return Response(item[0], media_type=item[1])


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

if __name__ == "__main__":      # python main.py  (Dockerfile e hosts que iniciam assim)
    import uvicorn
    # Porta 8000 fixa (a que o SnapDeploy detecta pelo EXPOSE). Se o painel injetar um
    # PORT diferente, escuta nele também: assim o health check passa nos dois casos.
    portas = [8000]
    try:
        extra = int(os.environ.get("PORT", "8000"))
        if extra not in portas:
            portas.append(extra)
    except ValueError:
        pass
    servers = [uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=p, log_level="info"))
               for p in portas]
    for srv in servers[1:]:
        threading.Thread(target=srv.run, daemon=True).start()
    servers[0].run()
