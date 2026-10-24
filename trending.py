"""Vídeos mais assistidos (inspiração de tema) pela API OFICIAL do YouTube.

- Sem busca: `chart=mostPopular` (custa 1 unidade da cota diária de 10.000).
- Com busca: `search?order=viewCount` na última semana (custa ~101 unidades).
Chave grátis: YOUTUBE_API_KEY (Google Cloud -> ativar "YouTube Data API v3" -> Credenciais).
O Ttoken usa isso só para mostrar títulos/visualizações/likes e sugerir IDEIAS ORIGINAIS.
Não baixa vídeos de terceiros.

Ordenação: score = views + (likes * 40) -> prioriza engajamento real.
"""
import os
import time

import requests

BASE = "https://www.googleapis.com/youtube/v3"
_CACHE, TTL = {}, 15 * 60


class SemChaveYoutube(Exception):
    pass


class ErroYoutube(Exception):
    pass


def chave_env():
    return (os.environ.get("YOUTUBE_API_KEY") or os.environ.get("YOUTUBE_KEY") or "").strip()


def _get(path, params, chave):
    r = requests.get(f"{BASE}/{path}", params={**params, "key": chave}, timeout=20)
    if r.status_code != 200:
        try:
            msg = r.json()["error"]["message"]
        except Exception:
            msg = r.text
        raise ErroYoutube(f"HTTP {r.status_code}: {str(msg)[:140]}")
    return r.json()


def _item(v):
    sn, st = v.get("snippet", {}), v.get("statistics", {})
    vid = v["id"] if isinstance(v["id"], str) else v["id"].get("videoId", "")
    thumbs = sn.get("thumbnails", {})
    views = int(st.get("viewCount", 0) or 0)
    likes = int(st.get("likeCount", 0) or 0)      # canal pode esconder os likes (vira 0)
    score = views + (likes * 40)
    return {"id": vid, "titulo": sn.get("title", ""), "canal": sn.get("channelTitle", ""),
            "views": views, "likes": likes, "score": score,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "thumb": (thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")}


def mais_vistos(regiao="BR", q="", limite=15, chave=None):
    chave = chave or chave_env()
    if not chave:
        raise SemChaveYoutube("Falta a chave do YouTube (YOUTUBE_API_KEY).")
    regiao = (regiao or "BR").upper()[:2]
    q = (q or "").strip()[:80]
    ck = (regiao, q.lower(), limite)
    hit = _CACHE.get(ck)
    if hit and hit[0] > time.time():
        return hit[1]
    if q:
        desde = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 7 * 86400))
        achados = _get("search", {"part": "snippet", "q": q, "type": "video",
                                  "order": "viewCount", "regionCode": regiao,
                                  "publishedAfter": desde, "maxResults": limite}, chave)
        ids = ",".join(i["id"]["videoId"] for i in achados.get("items", [])
                       if "videoId" in i.get("id", {}))
        itens = _get("videos", {"part": "snippet,statistics", "id": ids},
                     chave).get("items", []) if ids else []
    else:
        itens = _get("videos", {"part": "snippet,statistics", "chart": "mostPopular",
                                "regionCode": regiao, "maxResults": limite},
                     chave).get("items", [])
    out = sorted((_item(v) for v in itens), key=lambda x: -x["score"])
    _CACHE[ck] = (time.time() + TTL, out)
    return out
