"""Motor Ttokem v24 (local, determinístico).

Implementa a mesma ideia do prompt v24 (extrair críticos + decisões, codebook
curto, núcleo reconstruído, fail-closed), mas SEM gastar uma chamada de IA:
comprimir com um LLM custa tokens por si só (o prompt v24 sozinho tem ~400
tokens), então só vale a pena localmente.

Modos:
  light : texto natural enxuto (sem codebook). Serve para prompts de VÍDEO e IA.
  v24   : formato codebook + núcleo. Serve para IAs de texto com contexto longo.
  auto  : escolhe o menor resultado que passa na verificação fail-closed; se a
          economia for < MIN_SAVING devolve o original (passthrough).
"""
import math
import re
import string

MIN_SAVING = 0.02      # abaixo disso não compensa comprimir
MIN_CHARS_V24 = 600    # codebook só compensa em textos longos

# ----------------------------------------------------------------- críticos
_EXT = (r"py|js|ts|tsx|jsx|java|kt|go|rs|rb|php|c|cpp|h|yml|yaml|json|toml|"
        r"spec|md|sh|html|css|txt|log|csv|sql")
_STATUS = (r"failed|success|pending|error|timeout|erro|falhou|sucesso|"
           r"pendente|completed|cancelled")

_CRIT_RE = re.compile("|".join([
    r"https?://[^\s<>\"')\]]+",                                   # URL
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+",                              # e-mail
    r"\b10\.\d{4,9}/[^\s]+",                                      # DOI
    rf"[\w./-]+\.(?:{_EXT}):\d+",                                 # arquivo:linha
    rf"\b[\w/-]+\.(?:{_EXT})\b",                                  # arquivo
    r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\.[a-z_]\w*",                  # Classe.metodo
    r"\b[A-Za-z_][\w-]*=[^\s,;|]+",                               # chave=valor
    r"#\d+\b",                                                    # #123
    r"\b[A-Z]{2,}-\d[\w-]*",                                      # INC-2026-4821
    r"\b[a-z]{2,6}_[0-9a-f]{6,}\b",                               # txn_9f8a...
    r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b",       # hash
    r"\b[A-Z][A-Z0-9]*_[A-Z0-9_]+\b",                             # TIMEOUT_ERROR
    r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z| UTC)?)?",  # data
    r"\b\d{1,2}:\d{2}(?::\d{2})?(?: UTC)?",                       # hora
    r"\b\d+(?:[.,]\d+)?\s?(?:ms|seg|min|kb|mb|gb|fps|px|%|s|h)\b",  # unidades
    r"\b\d{3,}\b",                                                # números
    rf"\b(?i:{_STATUS})\b",                                       # status
    r"(?<=[a-zà-ú] )[A-Z][A-Za-z0-9]{3,}\b",                      # Empresa/sistema
]))

# --------------------------------------------------------------- decisões
_DECISION_RE = re.compile(
    r"decis|pr[oó]xim|\bpassos?\b|\ba[cç][aã]o\b|recomend|\bdeve|precisa|\bfa[cç]a\b|"
    r"execute|rollback|reprocess|verific|\babrir|notific|corrig|instal|"
    r"configur|clon|\bmust\b|\bshould\b|need to|\bnext\b|\btodo\b", re.IGNORECASE)

_STOP = set("""
a o as os um uma uns umas de da do das dos em na no nas nos por para pra com
sem sobre entre e ou mas que se ao aos à às pelo pela pelos pelas isso isto
esse essa esses essas este esta estes estas aquele aquela ele ela eles elas
eu nós você vocês meu minha seu sua nosso nossa já também ainda muito muita
bem então assim aqui ali lá só apenas bastante tipo né
the an of to in on at for with and or but that this these those it its is are
was were be been being very just also then so as by from
""".split())

_FILLERS = [
    r"por favor", r"gostaria de", r"eu queria", r"voc[eê] poderia", r"poderia",
    r"na verdade", r"basicamente", r"ou seja", r"de certa forma", r"meio que",
    r"digamos que", r"sabe", r"please", r"kindly", r"basically", r"actually",
    r"i would like to", r"could you", r"you know", r"in order to",
]
_FILLER_RE = re.compile(r"\b(?:" + "|".join(_FILLERS) + r")\b[,]?\s*", re.IGNORECASE)

# ------------------------------------------- dicionários por tipo (imagem/vídeo/código)
# Trocam frases LONGAS e redundantes por equivalentes curtos em linguagem natural (o gerador
# entende sem decodificar). Nunca apagam restrições: ver _RESTRICAO e _verifica_tipo.
_LIVROS = {
    "imagem": [
        (r"dramatically improve this photo into an? polished,? professional[- ]quality portrait",
         "professional portrait"),
        (r"re-?light the subject with (?:flattering )?soft directional key light(?: and gentle fill)?",
         "soft key light"),
        (r"so (?:that )?the face is bright, dimensional,? and clearly separated from the background",
         ", bright face, separated from background"),
        (r"add depth of field: render the background with natural, creamy optical blur so the subject pops",
         "shallow depth of field, creamy bokeh"),
        (r"and clean up background clutter and distracting bright spots", "clean background"),
        (r"apply an? rich,? film-?like colou?r grade with deep clean blacks,? luminous highlights,? "
         r"and vivid but natural colou?r", "film color grade, deep blacks, luminous highlights, natural vivid color"),
        (r"(?:highly|ultra|extremely)[- ]detailed|high[- ]quality", "detailed"),
        (r"corpo inteiro e bem enquadrado", "corpo inteiro"),
    ],
    "video": [
        (r"the camera (?:pans|moves|slides) smoothly (?:to the )?right", "pan right"),
        (r"the camera (?:pans|moves|slides) smoothly (?:to the )?left", "pan left"),
        (r"(?:the )?(?:subject|she|he|they) walks? (?:slowly )?(?:towards?|to) the camera",
         "walks toward camera"),
        (r"the camera (?:slowly )?(?:pushes|dollies|moves) in(?: toward| towards)?",
         "dolly-in toward"),
        (r"smooth camera orbit around", "orbit around"),
        (r"the camera tracks? backwards?", "tracking back"),
        (r"(?:with )?volumetric lighting (?:through|in) the (mist|fog|smoke)", r"volumetric light, \1"),
        (r"(?:a )?duration (?:of )?(\d+) seconds", r"\1 seconds"),
        (r"cinematic lighting", "cinematic light"),
    ],
    "codigo": [
        (r"make sure (?:that )?", "ensure "),
        (r"(?:i want|i need|i would like) you to ", ""),
        (r"quero que voc[eê] ", ""),
        (r"preciso que voc[eê] ", ""),
        (r"(?:that|which) (?:runs|run) in (?:a )?background thread", "in a background thread"),
        (r"(?:the )?github actions workflow", "GitHub Actions workflow"),
        (r"(?:write|create|build) (?:me )?an? (?:complete |full )?(?:python )?script (?:that|which|to) ",
         "script to "),
    ],
}
_LIVROS = {k: [(re.compile(a, re.IGNORECASE), b) for a, b in v] for k, v in _LIVROS.items()}

_PISTAS = {
    "codigo": r"\b(?:python|kivy|fastapi|apk|buildozer|endpoint|workflow|script|c[oó]digo|"
              r"fun[cç][aã]o|refator|json|http|thread|api)\b",
    "video": r"\b(?:v[ií]deo|video|c[aâ]mera|camera|pans?|dolly|drone|seconds|segundos|fps|"
             r"tiktok|reels|shorts|animate|anima[cç][aã]o|time-?lapse)\b",
    "imagem": r"\b(?:photo|foto|portrait|retrato|lighting|bokeh|image|imagem|logo|"
              r"depth of field|film[- ]like|color grade)\b",
}

# palavras que carregam RESTRIÇÃO: a contagem delas não pode diminuir depois da troca
_RESTRICAO = re.compile(
    r"\b(?:preserve|preserving|keep|keeping|same|exact|exactly|identical|only|never|without|"
    r"no|not|don't|do not|mantenha|manter|mesmo|mesma|exatamente|apenas|somente|sem|"
    r"n[aã]o|nunca)\b", re.IGNORECASE)


_PT = re.compile(r"\b(?:de|que|com|para|uma|um|não|nao|sem|meu|minha|da|em|os)\b",
                 re.IGNORECASE)


def detectar_tipo(text: str):
    """'imagem' | 'video' | 'codigo' | None (pelo maior número de pistas)."""
    placar = {k: len(re.findall(rx, text, re.IGNORECASE)) for k, rx in _PISTAS.items()}
    k = max(placar, key=placar.get)
    return k if placar[k] >= 1 else None


def aplicar_dicionario(text: str, tipo=None):
    """Troca frases longas pelas curtas do tipo. Retorna (texto, nº de trocas).
    Fail-closed: se qualquer crítico ou restrição sumir, devolve o texto original."""
    tipo = tipo if tipo in _LIVROS else detectar_tipo(text)
    if not tipo:
        return text, 0
    out, n = text, 0
    for rx, novo in _LIVROS[tipo]:
        out, k = rx.subn(novo, out)
        n += k
    # Geradores de imagem/vídeo (CLIP/T5) entendem prompt sem artigos: tira "the/a/an",
    # só em INGLÊS (em português o "a" é preposição) e nunca em código.
    if tipo in ("imagem", "video") and not _PT.search(out):
        out, k = re.subn(r"\b(?:the|an?)\s+", "", out, flags=re.IGNORECASE)
        n += k
    out = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"[ \t]{2,}", " ", out))
    if n == 0:
        return text, 0
    if len(_RESTRICAO.findall(out)) < len(_RESTRICAO.findall(text)):
        return text, 0
    if any(c not in out for c in extract_critical(text)):
        return text, 0
    return out, n


_ALPHABET = string.digits + string.ascii_uppercase + string.ascii_lowercase


def est_tokens(text: str) -> int:
    """Estimativa de tokens (~3.6 caracteres/token em pt-BR). Não é exata."""
    return max(1, math.ceil(len(text) / 3.6)) if text else 0


def _sentences(text: str):
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p and p.strip()]


def extract_critical(text: str):
    """Críticos únicos, na ordem de aparição, sem pontuação final solta."""
    seen, out = set(), []
    for m in _CRIT_RE.finditer(text):
        c = m.group(0).rstrip(".,;:)")
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _codes(n: int):
    """IDs de 1 caractere (62) e depois de 2 caracteres."""
    for ch in _ALPHABET:
        yield ch
    for a in string.digits + string.ascii_uppercase:
        for b in string.digits + string.ascii_uppercase:
            yield a + b


def _telegraph(sentence: str, ids: dict) -> str:
    """Remove palavras de ligação preservando críticos (trocados por §ID)."""
    protected = _FILLER_RE.sub("", sentence)
    for crit in sorted(ids, key=len, reverse=True):
        protected = protected.replace(crit, f"\x00{ids[crit]}\x00")
    words = []
    for w in re.split(r"(\x00[^\x00]+\x00|\s+)", protected):
        if not w or w.isspace():
            continue
        if w.startswith("\x00"):
            words.append("§" + w.strip("\x00"))
        elif w.lower().strip(".,;:!?()") in _STOP:
            continue
        else:
            words.append(w.strip(",;"))
    return " ".join(words).strip().rstrip(".")


def compress_light(text: str) -> str:
    t = _FILLER_RE.sub("", text)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    t = re.sub(r"([!?.,])\1+", r"\1", t)
    seen, out = set(), []
    for s in _sentences(t):
        key = re.sub(r"\W+", " ", s.lower()).strip()
        if key and key not in seen:
            seen.add(key)
            out.append(s)
    res = " ".join(out).strip()
    return re.sub(r"(^|[.!?]\s+)([a-zà-ú])", lambda m: m.group(1) + m.group(2).upper(), res)


def _build_v24(text: str):
    crit = extract_critical(text)
    ids = dict(zip(crit, _codes(len(crit))))
    sents = _sentences(text)
    decisions = [s for s in sents if _DECISION_RE.search(s)]
    nucleus_d = [_telegraph(s, ids) for s in decisions]
    codebook = "; ".join(f"{c}→{i}" for c, i in ids.items())
    core = "C:" + ",".join(ids.values())
    for d in nucleus_d:
        core += " | D:" + d
    body = f"[CODEBOOK]: {codebook}\n[NÚCLEO]: {core}"
    return body, crit, ids, decisions, nucleus_d


def _decode(core_text: str, ids: dict) -> str:
    inv = {v: k for k, v in ids.items()}
    out = core_text
    for code in sorted(inv, key=len, reverse=True):
        out = out.replace("§" + code, inv[code])
    return out


def _verify(body, crit, ids, decisions, nucleus_d):
    """Fail-closed: tudo que é crítico tem que sobreviver à decodificação."""
    lost = []
    core_line = body.split("[NÚCLEO]:", 1)[1]
    code_line = body.split("[NÚCLEO]:", 1)[0]
    for c in crit:
        if f"{c}→{ids[c]}" not in code_line or ids[c] not in core_line:
            lost.append(c)
    decoded = _decode(core_line, ids)
    ok_dec = 0
    for d in decisions:
        want = [m.group(0).rstrip(".,;:)") for m in _CRIT_RE.finditer(d)]
        want += [w for w in re.findall(r"\w+", d.lower())
                 if w not in _STOP and not w.isdigit()]
        miss = [w for w in want if w.lower() not in decoded.lower()]
        if miss:
            lost.append(f"decisão sem: {miss[0]}")
        else:
            ok_dec += 1
    return lost, ok_dec


_DECODE_HINT = "(decodifique §ID pelo CODEBOOK)\n"


def compress(text: str, mode: str = "auto", tipo=None) -> dict:
    """Devolve dict com `text` (o que mandar à IA) e métricas.
    `tipo`: 'imagem' | 'video' | 'codigo' (ou None = detectar) escolhe o dicionário."""
    text = (text or "").strip()
    orig = est_tokens(text)
    result = {"mode": "passthrough", "text": text, "original_tokens": orig,
              "compressed_tokens": orig, "saving_pct": 0.0, "lost": [],
              "critical": f"0/0", "decisions": "0/0", "formatted": None,
              "refusal": False, "tipo": None, "trocas": 0}
    if not text:
        return result

    cands = []
    trocas = 0
    if mode in ("light", "auto"):
        leve, trocas = aplicar_dicionario(compress_light(text), tipo)
        cands.append(("light", leve, [], None))
    if mode == "v24" or (mode == "auto" and len(text) >= MIN_CHARS_V24):
        body, crit, ids, dec, nd = _build_v24(text)
        lost, ok_dec = _verify(body, crit, ids, dec, nd)
        # Em AUTO o v24 só vale para texto de ocorrência/ticket (tem críticos E decisões).
        # Sem eles o núcleo fica vazio: "economizaria" apagando o pedido inteiro.
        if mode == "auto" and (not crit or not dec):
            lost = lost + ["v24 sem críticos/decisões: apagaria o conteúdo"]
        cands.append(("v24", _DECODE_HINT + body, lost,
                      (len(crit), len(dec), ok_dec)))

    best = None
    for name, out, lost, stats in cands:
        if lost:                       # fail-closed: descarta candidato
            continue
        tok = est_tokens(out)
        if best is None or tok < best[2]:
            best = (name, out, tok, stats)

    if best is None:
        if mode == "v24":
            result.update(refusal=True,
                          lost=cands[0][2] if cands else ["sem candidato"])
        return result

    name, out, tok, stats = best
    saving = 1 - tok / orig if orig else 0.0
    if mode == "auto" and saving < MIN_SAVING:
        return result
    result.update(mode=name, text=out, compressed_tokens=tok,
                  saving_pct=round(saving * 100, 1),
                  tipo=(tipo if tipo in _LIVROS else detectar_tipo(text)),
                  trocas=trocas if name == "light" else 0)
    if stats:
        n_crit, n_dec, ok_dec = stats
        result["critical"] = f"{n_crit}/{n_crit}"
        result["decisions"] = f"{ok_dec}/{n_dec}"
        result["formatted"] = (
            "⚡ [TTOKEM v24 — RECONSTRUÇÃO] ⚡\n"
            f"[ORIGINAL_TOKENS]: {orig}\n[COMPRIMIDO_TOKENS]: {tok}\n"
            f"[ECONOMIA]: {result['saving_pct']}%\n"
            f"[CRITICOS_PRESERVADOS]: {result['critical']}\n"
            f"[DECISOES_PRESERVADAS]: {result['decisions']}\n"
            f"[PERDIDOS]: nenhum\n{out.replace(_DECODE_HINT, '')}")
    return result
