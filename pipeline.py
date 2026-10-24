"""Pipeline único (usado pelo servidor E pelo app, no modo sem servidor):
ideia -> Ttokem (comprime só se compensar) -> maestro de IAs -> maestro de vídeo.

produzir() só depende de `requests` (via maestro/video) e de stdlib.
"""
import hashlib
import json
import re
import time

import ttokem
from maestro import AllProvidersFailed
from video import VideoFailed

IDIOMA_PADRAO = "Português do Brasil"


def limpar_idioma(v):
    """Aceita QUALQUER idioma escrito pelo usuário (ex.: 日本語, Swahili, Esperanto), só tira
    símbolos estranhos e limita o tamanho (evita injetar instruções no prompt)."""
    v = re.sub(r"[^\w \-().,/]", "", str(v or ""), flags=re.UNICODE).strip()[:40]
    return v or IDIOMA_PADRAO


SYSTEM = (
    "Você cria conteúdo para vídeos curtos de redes sociais. Responda SOMENTE "
    "com JSON válido, sem texto fora dele, com as chaves: "
    '"titulo" (curto), "roteiro" (3-5 cenas curtas), "legenda" (pronta para '
    'postar, em {IDIOMA}, com gancho e chamada para ação), '
    '"hashtags" (lista de 5 a 8 strings com #), "prompt_video" (descrição '
    "visual da cena em INGLÊS, até 350 caracteres, sem texto na tela).")

SYSTEM_ANUNCIO = (
    "Você é diretor de arte de comerciais curtos para redes sociais. Responda SOMENTE "
    "com JSON válido, com as chaves: "
    '"titulo" (nome da campanha), "roteiro" (4 cenas: 1 gancho visual impactante que '
    'remete ao produto, 2 ambiente/fachada, 3 close do produto com luz cinematográfica, '
    '4 uso/unboxing e chamada para ação), "legenda" (copy de venda em {IDIOMA}: '
    'gancho, benefício, prova, chamada para comprar), "hashtags" (5 a 8 com #), '
    '"prompt_video" (INGLÊS, até 350 caracteres, estilo cinematic commercial: movimento de '
    "câmera (slow dolly-in, orbit), iluminação, produto em destaque, partículas/brilho, "
    "sem texto na tela; se houver foto do produto, descreva como ANIMAR a foto mantendo o "
    "produto idêntico).")

LLM_CACHE, LLM_CACHE_TTL = {}, 6 * 3600      # mesma ideia = mesmo roteiro, sem gastar crédito
VIDEO_CACHE, VIDEO_CACHE_TTL = {}, 3600      # links de vídeo expiram em ~1h


def parse_json(text: str):
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    try:
        return json.loads(m.group(0) if m else t)
    except (ValueError, AttributeError):
        return None


def produzir(ideia, plataforma, llm, videos, report, regenerar=False,
             tipo="normal", imagem_url="", sem_video=False, idioma=IDIOMA_PADRAO):
    """Roda o fluxo todo. `report(**campos)` recebe o progresso e o resultado.
    Sempre termina com report(status="pronto") ou report(status="erro")."""
    try:
        # 1) Ttokem: só comprime se realmente economiza (ideias curtas passam direto)
        comp = ttokem.compress(ideia, "auto")
        report(etapa="IA escrevendo roteiro e legenda", passo=1,
               tokens={"antes": comp["original_tokens"],
                       "depois": comp["compressed_tokens"],
                       "economia_pct": comp["saving_pct"], "modo": comp["mode"]})

        # 2) Maestro de IAs (troca sozinho se um crédito acabar)
        anuncio = tipo == "anuncio"
        idioma = limpar_idioma(idioma)
        ckey = hashlib.sha1(
            f"{tipo}|{idioma}|{plataforma}|{comp['text'].lower()}".encode()).hexdigest()
        hit = LLM_CACHE.get(ckey)
        if hit and not regenerar and hit[0] > time.time():
            data, api_llm = hit[1], hit[2] + " (cache)"
        else:
            try:
                raw, api_llm = llm.chat([
                    {"role": "system", "content": (SYSTEM_ANUNCIO if anuncio else SYSTEM
                                                   ).replace("{IDIOMA}", idioma)},
                    {"role": "user",
                     "content": f"Plataforma: {plataforma}\nIdioma da legenda e do roteiro: "
                                f"{idioma}\nIdeia: {comp['text']}"}])
            except AllProvidersFailed as e:
                report(status="erro", erro=f"Nenhuma IA disponível: {e}")
                return
            data = parse_json(raw) or {"legenda": raw, "prompt_video": ideia}
            LLM_CACHE[ckey] = (time.time() + LLM_CACHE_TTL, data, api_llm)
        tags = data.get("hashtags") or []
        if isinstance(tags, str):
            tags = tags.split()
        legenda = str(data.get("legenda", "")).strip()
        texto = (legenda + ("\n\n" + " ".join(tags) if tags else "")).strip()
        eta = {}
        try:
            prox = videos.proxima()
            if prox:
                eta = {"gerador": prox[0], "eta_min": prox[1], "eta_max": prox[2]}
        except Exception:
            pass
        report(api_llm=api_llm, titulo=data.get("titulo", ""),
               roteiro=data.get("roteiro", ""), texto=texto,
               etapa="Gerando vídeo", passo=2, **eta)

        if sem_video:       # o usuário já tem o vídeo (feito em outro app): só legenda
            report(status="pronto", video_url="", api_video="seu video",
                   etapa="Pronto", passo=3)
            return

        # 3) Maestro de vídeo (prompt em linguagem natural, NUNCA o codebook)
        # Regra de ouro: o gerador de vídeo NUNCA recebe o texto bruto do usuário. Se a IA
        # não devolveu o resumo visual (prompt_video), para AQUI, antes de gastar vídeo.
        prompt_video = str(data.get("prompt_video") or "").strip()[:500]
        if len(prompt_video) < 8 or prompt_video.lower() == str(ideia).strip().lower():
            LLM_CACHE.pop(ckey, None)
            report(status="erro", erro="A IA nao devolveu o resumo visual do video; nao gastei "
                   "nenhum credito de video. Toque em GERAR de novo.")
            return
        vhit = None if imagem_url else VIDEO_CACHE.get(prompt_video)
        extra_aviso = ""
        try:
            if vhit and not regenerar and vhit[0] > time.time():
                url, api_video, dl_headers = vhit[1], vhit[2] + " (cache)", None
            else:
                if imagem_url:
                    try:
                        url, api_video, dl_headers = videos.generate(prompt_video, imagem_url)
                    except VideoFailed as e:   # sem gerador de foto: tenta só com texto
                        extra_aviso = f"Foto nao usada ({str(e)[:90]}); video feito so pelo texto."
                        url, api_video, dl_headers = videos.generate(prompt_video)
                else:
                    url, api_video, dl_headers = videos.generate(prompt_video)
                if not dl_headers and not imagem_url:
                    VIDEO_CACHE[prompt_video] = (time.time() + VIDEO_CACHE_TTL, url, api_video)
            if dl_headers:      # o arquivo exige token no download
                report(_src=url, _dl=dl_headers)
            extra = {"aviso": extra_aviso} if extra_aviso else {}
            if str(api_video).startswith("pexels"):
                extra["aviso"] = ("Video de banco gratis (Pexels), nao gerado por IA: "
                                  "serve de apoio para a sua legenda.")
            report(status="pronto", video_url=url, api_video=api_video,
                   etapa="Pronto", passo=3, **extra)
        except VideoFailed as e:
            # a legenda continua válida mesmo sem vídeo
            report(status="pronto", video_url="", api_video="nenhum",
                   aviso=f"Vídeo indisponível agora: {e}",
                   etapa="Pronto (sem vídeo)", passo=3)
    except Exception as e:      # nunca deixar o job preso em "processando"
        report(status="erro", erro=f"Falha inesperada: {e}")
