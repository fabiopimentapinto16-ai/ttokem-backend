"""Modo Cineasta: transforma UMA ideia em 2-4 cenas e gera um vídeo curto por cena.

Versão segura:
- o roteiro das cenas é só texto (grátis);
- cada cena usa 1 geração de vídeo, então o total é mostrado ANTES de começar (MAX_CENAS);
- os vídeos NÃO são colados num filme só (isso exigiria ffmpeg); o app mostra as cenas em ordem;
- se uma cena falhar, as outras continuam e o aviso diz qual falhou.
"""
import json
import re

from video import VideoFailed

MIN_CENAS, MAX_CENAS, PADRAO = 2, 4, 3

SYSTEM = (
    "Você é um diretor de cinema. Divida a ideia em {N} cenas curtas e visuais, uma ação por "
    "cena, mantendo personagens, lugar e estilo consistentes entre as cenas. Responda SOMENTE "
    'com JSON válido: {{"titulo": "...", "cenas": [{{"descricao": "(em {IDIOMA})", '
    '"prompt_video": "(em INGLÊS, 1-2 frases; repita estilo e personagens em toda cena)"}}]}}')


def limitar(n) -> int:
    try:
        n = int(n)
    except (TypeError, ValueError):
        n = PADRAO
    return max(MIN_CENAS, min(MAX_CENAS, n))


def _parse(texto: str):
    t = re.sub(r"^```(?:json)?|```$", "", (texto or "").strip(), flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", t, re.DOTALL)
    try:
        return json.loads(m.group(0) if m else t)
    except ValueError:
        return None


def planejar(llm, ideia: str, n: int, idioma: str):
    """-> (titulo, [{'descricao','prompt_video'}] com exatamente n cenas, api_usada)."""
    n = limitar(n)
    api = ""
    cenas, titulo = [], ""
    try:
        raw, api = llm.chat([{"role": "system",
                              "content": SYSTEM.replace("{N}", str(n)).replace("{IDIOMA}", idioma)},
                             {"role": "user", "content": f"Ideia: {ideia}"}])
        data = _parse(raw) or {}
        titulo = str(data.get("titulo") or "")[:120]
        for c in (data.get("cenas") or []):
            if isinstance(c, dict) and str(c.get("prompt_video") or "").strip():
                cenas.append({"descricao": str(c.get("descricao") or "")[:300],
                              "prompt_video": str(c["prompt_video"])[:500]})
    except Exception:       # IA de texto fora do ar: usa a própria ideia, sem perder a geração
        cenas = []
    cenas = cenas[:n]
    while len(cenas) < n:   # completa (ou cria) as cenas que faltarem
        i = len(cenas) + 1
        cenas.append({"descricao": f"Cena {i}",
                      "prompt_video": f"{ideia[:430]} (scene {i} of {n})"})
    return titulo, cenas, api


def produzir(ideia, n, llm, videos, report, idioma="Português do Brasil"):
    n = limitar(n)
    report(etapa="Roteirizando as cenas", passo=1, total_cenas=n)
    titulo, cenas, api_llm = planejar(llm, ideia, n, idioma)
    saida = [{"n": i + 1, "descricao": c["descricao"], "prompt_video": c["prompt_video"],
              "video_url": "", "gerador": "", "erro": ""} for i, c in enumerate(cenas)]
    report(titulo=titulo, api_llm=api_llm, cenas=[dict(c) for c in saida])
    ok = 0
    for i, c in enumerate(saida):
        report(etapa=f"Gerando cena {i + 1} de {n}", passo=2 + i)
        try:
            url, gerador, dl = videos.generate(c["prompt_video"])
            c.update(video_url=url, gerador=gerador, _dl=dl)
            ok += 1
        except VideoFailed as e:
            c["erro"] = str(e)[:200]
        report(cenas=[dict(x) for x in saida])
    falhas = [c["n"] for c in saida if c["erro"]]
    aviso = (f"Cena(s) {', '.join(map(str, falhas))} falharam; as outras foram geradas."
             if falhas and ok else "")
    if not ok:
        report(status="erro", erro="Nenhuma cena foi gerada: " + (saida[0]["erro"] or "sem gerador"))
        return
    report(status="pronto", aviso=aviso)
