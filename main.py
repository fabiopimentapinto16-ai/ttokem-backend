
import os, requests, time
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

class Req(BaseModel):
    ideia: str

# Prompt Ttokem v24
TTOKEM_V24_PROMPT = r"""
# ROLE
Voce e o Ttokem v24 - motor de compressao por RECONSTRUCAO. Sua funcao e extrair os componentes essenciais do texto e RECONSTRUIR um nucleo ultra-denso.
# MISSAO
1. Extrair TODOS os itens criticos.
2. Extrair TODAS as decisoes e proximos passos.
3. Gerar um CODEBOOK ultra-curto.
4. RECONSTRUIR o nucleo do zero.
5. Descartar TODO o resto.
6. Se algo critico nao aparecer, RECUSE.
# FORMATO DE SAIDA
[ECONOMIA]: <percentual>%
[CODEBOOK]: <critico -> ID>
[NUCLEO]: C:<IDs> | D:<decisoes>
"""

def processar_com_ttokem(ideia):
    prompt = f"{TTOKEM_V24_PROMPT}\n\nTexto do usuario: {ideia}"
    api_key = os.environ.get('GEMINI_KEY')
    
    if not api_key: 
        return "Chave do Gemini nao configurada no servidor.", "Erro"
    
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={api_key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        res = requests.post(url, json=payload, timeout=20)
        if res.status_code == 200:
            return res.json()["candidates"][0]["content"]["parts"][0]["text"], "Gemini + Ttokem v24"
    except Exception as e:
        return f"Erro no Ttokem: {str(e)}", "Erro"

PROXY_URL = os.environ.get("PROXY_URL", "http://localhost:8080/v1")

def gerar_video_via_proxy(prompt):
    try:
        payload = {"model": "wan-fast", "prompt": prompt}
        res = requests.post(f"{PROXY_URL}/videos", json=payload, timeout=30)
        res.raise_for_status()
        job_id = res.json()["id"]

        for _ in range(60): 
            status_res = requests.get(f"{PROXY_URL}/videos/{job_id}")
            status_data = status_res.json()
            if status_data.get("status") == "completed":
                return f"{PROXY_URL}/videos/{job_id}/content", "Proxy Gratuito"
            elif status_data.get("status") == "failed":
                return None, "Falha no proxy"
            time.sleep(5)
        return None, "Tempo esgotado no proxy"
    except Exception as e:
        return None, f"Erro no proxy: {str(e)}"

@app.post("/api/gerar")
def gerar(req: Req):
    texto_ttokem, api_llm = processar_com_ttokem(req.ideia)
    video_url, api_video = gerar_video_via_proxy(texto_ttokem)
    
    if video_url:
        return {"success": True, "texto": texto_ttokem, "video_url": video_url, "api_llm": api_llm, "api_video": api_video}
    else:
        return {"success": False, "error": api_video}
