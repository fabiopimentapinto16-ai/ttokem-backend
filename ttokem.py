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
from collections import Counter

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
    r"i would like to", r"could you", r"you know",
]   # "in order to" NÃO é enchimento: tirar apagava o "to" e juntava dois verbos ("run ship")
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
         "add shallow depth of field, creamy bokeh"),
        (r"and clean up background clutter and distracting bright spots", "clean background"),
        (r"apply an? rich,? film-?like colou?r grade with deep clean blacks,? luminous highlights,? "
         r"and vivid but natural colou?r", "film color grade, deep blacks, luminous highlights, vivid but natural color"),
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

# --- Adaptação do NHHC-4F: os 4 fatores (ROLE, CONTEXT, TASK, CONSTRAINTS) como equivalências
# APROVADAS em linguagem natural (nunca códigos tipo "py:fastapi"). Cada entrada só tira o que
# é redundante por definição (ex.: FastAPI já é Python, APK já é Android) e passa pelo mesmo
# auditor fail-closed (fatos, lógica, verbos, restrições). Fator -> (tipos, regex, curto).
NHHC4F = {
    "ROLE": [
        (("codigo",), r"(?:as )?an? expert python developer", "python expert"),
    ],
    "TASK": [
        (("codigo",), r"\bpython script using fastapi\b", "fastapi script"),
        (("codigo",), r"\b(?:an? )?android apk via buildozer\b", "buildozer apk"),
        (("codigo",), r"\b(?:an? )?android apk\b", "apk"),
    ],
    "CONTEXT": [
        (("codigo", "video", "imagem"), r"\bin order to\b", "to"),
        (("codigo", "video", "imagem"), r"\bdue to the fact that\b", "because"),
        (("codigo", "video", "imagem"), r"\ba large number of\b", "many"),
        (("codigo", "video", "imagem"), r"\bis able to\b", "can"),
        (("codigo", "video", "imagem"), r"\bat this point in time\b", "now"),
        (("codigo", "video", "imagem"), r"\beach and every\b", "each"),
        (("codigo", "video", "imagem"), r"\bthe vast majority of\b", "most"),
        (("codigo", "video", "imagem"), r"\bfor the purpose of\b", "for"),
        (("codigo", "video", "imagem"), r"\bwith (?:regard|respect) to\b", "about"),
    ],
    "CONSTRAINTS": [
        (("codigo", "video", "imagem"), r"\bit is important to (?:note|remember) that\s*", ""),
    ],
}
for _fator, _itens in NHHC4F.items():
    for _tipos, _rx, _novo in _itens:
        for _t in _tipos:
            _LIVROS[_t].append((re.compile(_rx, re.IGNORECASE), _novo))

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


# ------------------------------------------------- conferência de dados (fail-closed)
# Fatos = e-mails, URLs, #ids, e qualquer palavra com dígito ou "_" (123, ID-a-1, snake_case).
_FACT = re.compile(r"https?://\S+|[\w.+-]+@[\w-]+\.[\w.]+|#\w+|\b\w*[\d_]\w*(?:[-.:/]\w+)*\b")
# Lógica = palavras que invertem ou condicionam o sentido; a contagem não pode cair.
_LOGIC = re.compile(r"\b(?:not|no|never|only|or|but|if|nunca|não|nao|apenas|somente|ou|mas|se|sem)\b",
                    re.IGNORECASE)
_VERBOS = re.compile(r"\b(?:create|generate|build|write|make|gere|crie|fa[cç]a|entregue|aja|"
                     r"escreva|resuma|envie|send|add|remove|delete|update|explain|show|run|open|"
                     r"abra|explique|mostre|adicione|remova|atualize)\b", re.IGNORECASE)


def semantica(orig: str, out: str) -> dict:
    """Mede se fatos, lógica e verbos de ação do original sobrevivem em `out`."""
    fatos = {f.rstrip(".,;:)") for f in _FACT.findall(orig)}
    f_ok = sum(1 for f in fatos if f in out)
    lo = Counter(m.lower() for m in _LOGIC.findall(orig))
    ln = Counter(m.lower() for m in _LOGIC.findall(out))
    l_ok = sum(min(lo[k], ln[k]) for k in lo)
    vo = {m.lower() for m in _VERBOS.findall(orig)}
    vn = {m.lower() for m in _VERBOS.findall(out)}
    v_ok = len(vo & vn)
    return {"fatos": f"{f_ok}/{len(fatos)}", "logica": f"{l_ok}/{sum(lo.values())}",
            "verbos": f"{v_ok}/{len(vo)}",
            "intacta": f_ok == len(fatos) and l_ok == sum(lo.values()) and v_ok == len(vo)}


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
    if any(c not in out for c in extract_critical(text)) or not semantica(text, out)["intacta"]:
        return text, 0
    return out, n


# ------------------------------------------------------------------ modelos prontos
# Frases-padrão que você repete viram uma referência curta (@id.n ou @id). A referência é
# expandida de volta ANTES do gerador, então nada se perde. Só casa com frases quase iguais
# (>= 80% das palavras), e a troca é cancelada se uma restrição ou crítico sumir.
PRESETS = {
    "foto_pro": {"tipo": "imagem", "frases": [
        "Dramatically improve this photo into a polished, professional-quality portrait while keeping the same person, same pose, same clothing, and same location.",
        "Re-light the subject with flattering soft directional key light and gentle fill so the face is bright, dimensional, and clearly separated from the background — eliminate flat, dull, or overhead lighting.",
        "Add depth of field: render the background with natural, creamy optical blur so the subject pops, and clean up background clutter and distracting bright spots.",
        "Apply a rich, film-like color grade with deep clean blacks, luminous highlights, and vivid but natural color.",
        "Critical constraints: preserve the person's exact same face, eyes, skin and appearance."]},
    "produto_cinema": {"tipo": "video", "frases": [
        "Create a cinematic commercial product shot with the product as the hero, centered, on a dark reflective surface.",
        "Soft rim light, shallow depth of field, slow dolly-in toward the product, subtle golden particles floating in the air, glossy reflections.",
        "Keep the product label, colors and logo exactly identical to the reference, no text overlay, no watermark.",
        "Vertical 9:16, smooth motion, 5 seconds."]},
    "retrato_corporativo": {"tipo": "imagem", "frases": [
        "Professional corporate portrait with a softly blurred light gray background, gentle side lighting and natural color.",
        "Keep exactly the same face, clothing and pose as the original photo, do not alter the face shape or the eyes, keep natural skin texture without over-smoothing."]},
    "app_kivy_apk": {"tipo": "codigo", "frases": [
        "Write a Python Kivy app that builds into an Android APK with buildozer, and a GitHub Actions workflow on ubuntu-22.04 that uploads the APK as an artifact.",
        "Run network calls in a background thread and update the UI only through Clock.schedule_once.",
        "Do not remove any existing feature, keep the existing tabs unchanged, and show only the final code."]},
}
_PRESET_LIM = 0.80


def _palavras(t: str):
    return set(re.findall(r"[\w'-]+", t.lower()))


def _sim(a: str, b: str) -> float:
    A, B = _palavras(a), _palavras(b)
    return len(A & B) / len(A | B) if A and B else 0.0


def expandir_presets(text: str) -> str:
    """@id -> preset inteiro; @id.n -> frase n (1..). Referência desconhecida fica como está."""
    def troca(m):
        pre = PRESETS.get(m.group(1))
        if not pre:
            return m.group(0)
        if m.group(2):
            i = int(m.group(2)) - 1
            return pre["frases"][i] if 0 <= i < len(pre["frases"]) else m.group(0)
        return " ".join(pre["frases"])
    return re.sub(r"@([a-z_]+)(?:\.(\d+))?", troca, text)


def aplicar_presets(text: str, tipo=None):
    """Troca frases idênticas às dos modelos por referências (Rua Neural v2: árvore com
    herança de restrições e limite de 95%). Retorna (texto, nº de frases trocadas)."""
    import rua_neural
    res, n, _ = rua_neural.BIBLIOTECA.selecionar(text, tipo)
    return res, n


def _aplicar_presets_v1(text: str, limite: float = None):
    """Versão antiga (limite de 80%, sem árvore): mantida só para comparar no backtest."""
    _lim = limite if limite is not None else _PRESET_LIM
    sents = _sentences(text)
    out, n, usados = [], 0, {}
    for sn in sents:
        ref = None
        for pid, pre in PRESETS.items():
            for k, fr in enumerate(pre["frases"], 1):
                if _sim(sn, fr) >= _lim:
                    ref = (pid, k)
                    break
            if ref:
                break
        if ref:
            out.append(ref)
            usados.setdefault(ref[0], []).append(ref[1])
            n += 1
        else:
            out.append(sn)
    final, i = [], 0
    while i < len(out):
        it = out[i]
        if isinstance(it, tuple):
            pid = it[0]
            seq = []
            while i < len(out) and isinstance(out[i], tuple) and out[i][0] == pid:
                seq.append(out[i][1]); i += 1
            total = len(PRESETS[pid]["frases"])
            final.append(f"@{pid}" if seq == list(range(1, total + 1))
                         else " ".join(f"@{pid}.{k}" for k in seq))
        else:
            final.append(it); i += 1
    res = " ".join(final)
    if n == 0:
        return text, 0
    exp = expandir_presets(res)
    if len(_RESTRICAO.findall(exp)) < len(_RESTRICAO.findall(text)) or \
            any(c not in exp for c in extract_critical(text)) or not semantica(text, exp)["intacta"]:
        return text, 0
    return res, n


# ------------------------------------------------------------------ modo agressivo
# Telegráfico: tira palavras de ligação e adjetivos de enfeite; NUNCA tira restrições,
# números, unidades, nomes e termos técnicos. Cancela tudo se algo crítico sumir.
_ENFEITE = set("""very really extremely highly truly quite rather slightly gently subtly softly
flattering beautiful stunning amazing gorgeous polished natural naturally clearly distracting
overall simply just entire whole nice good great perfect perfectly smoothly smooth lovely
muito bem lindo linda incrivel incrível perfeito perfeita""".split())


# Frases verbosas comuns (pt/en) -> equivalentes curtos de mesmo sentido. Passa pelo mesmo
# auditor fail-closed: se um fato, verbo de acao, palavra de logica ou restricao sumir, e descartado.
_GERAL = [(re.compile(a, re.IGNORECASE), b) for a, b in [
    (r"\bem rela[cç][aã]o a\b", "sobre"), (r"\bdevido ao fato de que\b", "porque"),
    (r"\ba fim de\b", "para"), (r"\bno momento atual\b", "agora"),
    (r"\bem um futuro pr[oó]ximo\b", "em breve"), (r"\bfazer uso de\b", "usar"),
    (r"\bleve em considera[cç][aã]o\b", "considere"), (r"\bpode me ajudar a\b", ""),
    (r"\bse n[aã]o for incomodar,?\s*", ""), (r"\bdesde j[aá] (?:agrade[cç]o|obrigad[oa])\.?\s*", ""),
    (r"\bmuito obrigad[oa]\.?\s*", ""), (r"\bobrigad[oa]\.?\s*", ""),
    (r"\bgostaria que voc[eê]\s+", ""), (r"\bi was wondering if you could\s+", ""),
    (r"\bthanks in advance\.?\s*", ""), (r"\bthank you(?: so much)?\.?\s*", ""),
    (r"\bfeel free to\s+", ""), (r"\bin the event that\b", "if"),
    (r"\bprior to\b", "before"), (r"\bsubsequent to\b", "after"),
    (r"\ba (?:large )?number of\b", "several"), (r"\bin spite of the fact that\b", "although"),
    (r"\bmake use of\b", "use"), (r"\btake into consideration\b", "consider"),
    (r"\bis going to\b", "will"), (r"\bin the near future\b", "soon"),
    (r"\bat the present time\b", "now"), (r"\bit is possible that\b", "may"),
    (r"\bas well as\b", "and"), (r"\bin addition,?\s*", ""), (r"\bi need you to\s+", ""),
]]


def aplicar_geral(text: str):
    """Aplica _GERAL; devolve (texto, n). Fail-closed: devolve o original se algo sumir."""
    out, n = text, 0
    for rx, novo in _GERAL:
        out, k = rx.subn(novo, out)
        n += k
    out = re.sub(r"\s+([,.;:])", r"\1", re.sub(r"[ \t]{2,}", " ", out)).strip()
    if n == 0:
        return text, 0
    if len(_RESTRICAO.findall(out)) < len(_RESTRICAO.findall(text)) or \
            any(c not in out for c in extract_critical(text)) or not semantica(text, out)["intacta"]:
        return text, 0
    return out, n



def compress_agressivo(text: str, tipo=None):
    original = text
    text, _ = aplicar_geral(text)
    base = compress_light(text)
    base = re.sub(r"\bin order to\b", "to_", base, flags=re.IGNORECASE)   # finalidade: mantém "to"
    base, _ = aplicar_dicionario(base, tipo)
    crit = set(extract_critical(original))
    palavras, vistas = [], set()
    toks = [w for w in re.split(r"(\s+)", base) if w and not w.isspace()]
    for idx, w in enumerate(toks):
        nu = w.lower().strip(".,;:!?()\"")
        prox = toks[idx + 1].lower().strip(".,;:!?()\"") if idx + 1 < len(toks) else ""
        eh_crit = any(c in w for c in crit)
        antes_restricao = bool(_RESTRICAO.fullmatch(prox))      # "do" de "do not", "is" de "is only"
        if (eh_crit or _RESTRICAO.fullmatch(nu) or _LOGIC.fullmatch(nu) or antes_restricao
                or re.search(r"\d", w) or _VERBOS.fullmatch(nu) or _FACT.fullmatch(w.strip(".,;:)"))
                or not (nu in _STOP or nu in _ENFEITE)):
            chave = nu
            if not eh_crit and chave in vistas and len(chave) > 3 and not re.search(r"\d", w):
                continue                                   # palavra repetida
            vistas.add(chave)
            palavras.append(w.rstrip(".;"))
    out = re.sub(r"\s+([,.;:])", r"\1", " ".join(palavras))
    out = re.sub(r"[,;]\s*(?=[,;])", "", out).strip(" ,;").replace("to_", "to")
    base = base.replace("to_", "to")
    if len(_RESTRICAO.findall(out)) < len(_RESTRICAO.findall(original)) or \
            any(c not in out for c in crit) or not semantica(original, out)["intacta"]:
        return base
    return out


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


def compress(text: str, mode: str = "auto", tipo=None, presets: bool = False) -> dict:
    """Devolve dict com `text` (o que mandar à IA) e métricas.
    `tipo`: 'imagem' | 'video' | 'codigo' (ou None = detectar) escolhe o dicionário.
    `presets=True` permite referências @id (quem recebe precisa chamar expandir_presets;
    o campo `expandido` já vem pronto). Modos: auto | light | v24 | agressivo."""
    text = (text or "").strip()
    orig = est_tokens(text)
    result = {"mode": "passthrough", "text": text, "original_tokens": orig,
              "compressed_tokens": orig, "saving_pct": 0.0, "lost": [],
              "critical": f"0/0", "decisions": "0/0", "formatted": None,
              "refusal": False, "tipo": None, "trocas": 0,
              "expandido": text, "semantica": semantica(text, text)}
    if not text:
        return result

    cands = []
    trocas = 0
    if presets and mode in ("agressivo", "auto"):
        pre, npre = aplicar_presets(text)
        if npre:                       # texto com referências @id (expandir antes de usar)
            cands.append(("preset", pre, [], None))
    if mode == "agressivo":
        cands.append(("agressivo", compress_agressivo(text, tipo), [], None))
    if mode in ("light", "auto", "agressivo"):
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
        if name != "v24" and not semantica(
                text, expandir_presets(out) if name == "preset" else out)["intacta"]:
            continue                   # perdeu fato, lógica ou verbo: não usa
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
    if mode in ("auto", "agressivo") and saving < MIN_SAVING:
        return result
    result.update(mode=name, text=out, compressed_tokens=tok,
                  saving_pct=round(saving * 100, 1),
                  tipo=(tipo if tipo in _LIVROS else detectar_tipo(text)),
                  trocas=trocas if name == "light" else 0,
                  expandido=expandir_presets(out) if name == "preset" else out)
    result["semantica"] = semantica(text, result["expandido"])
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
