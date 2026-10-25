"""Comandos do Ttoken Studio (/codigo, /eficiente, /video ...).

A pessoa só escreve a frase normal ("quero esse código com eficiência"). Este módulo
descobre sozinho qual comando serve, escolhe o ramo da Rua Neural (tipo) e devolve uma
instrução CURTA e fixa para a IA de texto, no lugar de um prompt grande escrito à mão.

Regras que não mudam:
- NUNCA altera o texto do usuário (só tira um /comando digitado de propósito no começo).
- A instrução vai só para a IA de texto (system/estilo). O gerador de vídeo nunca a recebe.
- Comando desconhecido ("/health" no meio da frase, "/api") continua sendo texto normal.
"""
import re

# id -> (tipo da Rua Neural ou None, instrução curta, pistas regex PT/EN)
CATALOGO = {
    "codigo": ("codigo",
               "Entregue código completo e funcional em um único bloco; "
               "explique só o que não for óbvio.",
               r"\b(?:c[oó]digo|script|fun[cç][aã]o|classe|api|endpoint|backend|kivy|fastapi|"
               r"python|javascript|bug|programa|code|function|class|refactor|refatorar)\b"),
    "eficiente": (None,
                  "Priorize eficiência: menor complexidade e menos consultas/laços "
                  "desnecessários; informe a complexidade em 1 linha.",
                  r"\b(?:efici[eê]ncia|eficiente|otimiz\w+|perform\w+|r[aá]pido|mais leve|"
                  r"efficien\w+|optimi[sz]\w+|faster|fast)\b"),
    "depurar": (None,
                "Ache a causa raiz do erro e mostre a correção mínima.",
                r"\b(?:erro|error|traceback|exception|falha|n[aã]o funciona|quebrou|crash|"
                r"debug\w*|depur\w+|corrig\w+|fix)\b"),
    "video": ("video",
              "Roteiro com cenas visuais curtas e concretas, uma ação por cena.",
              r"\b(?:v[ií]deo|reels?|tiktok|shorts?|roteiro|cena|cinematogr\w+|animação|"
              r"animation|clip)\b"),
    "imagem": ("imagem",
               "Descreva sujeito, cenário, luz e estilo de forma concreta.",
               r"\b(?:imagem|foto|ilustra[cç][aã]o|avatar|retrato|poster|pôster|image|"
               r"picture|photo)\b"),
    "legenda": (None,
                "Escreva legenda curta e natural, com gancho no começo e hashtags no fim.",
                r"\b(?:legenda|caption|hashtags?|post|postar|descri[cç][aã]o do post)\b"),
    "resumir": (None,
                "Resuma mantendo todos os números, nomes e decisões; nada inventado.",
                r"\b(?:resum\w+|sintetiz\w+|em poucas palavras|summar\w+|tl;?dr)\b"),
    "explicar": (None,
                 "Explique de forma simples, com um exemplo curto.",
                 r"\b(?:explic\w+|o que [eé]|como funciona|por que|explain|how does)\b"),
    "traduzir": (None,
                 "Traduza com fidelidade total; mantenha nomes, números e tom.",
                 r"\b(?:traduz\w*|translate|tradu[cç][aã]o)\b"),
    "revisar": (None,
                "Corrija e melhore a escrita sem mudar o sentido nem os fatos.",
                r"\b(?:revis\w+|corrija o texto|melhore o texto|proofread|rewrite|reescrev\w+)\b"),
    "anuncio": (None,
                "Foco em benefício claro, prova e uma chamada para ação única.",
                r"\b(?:an[uú]ncio|propaganda|vender|venda|promo\w+|ad copy|advert\w+)\b"),
}
_PISTAS = {k: re.compile(v[2], re.IGNORECASE) for k, v in CATALOGO.items()}
_TIPOS = ("codigo", "video", "imagem")      # ramos exclusivos: só um vence
MAX_COMANDOS = 3
_CMD_INICIAL = re.compile(r'^\s*"?/([a-zA-Zçãé]+)\b')


def _explicitos(texto: str):
    """Comandos digitados no começo (/codigo /eficiente frase). Devolve (ids, resto)."""
    ids, resto = [], texto
    while True:
        m = _CMD_INICIAL.match(resto)
        if not m or m.group(1).lower() not in CATALOGO:
            break
        ids.append(m.group(1).lower())
        resto = resto[m.end():]
    if ids:
        resto = resto.strip()
        if len(resto) >= 2 and resto[0] == '"' and resto[-1] == '"':   # /resumir "texto"
            resto = resto[1:-1].strip()
    return ids, resto


def _automaticos(texto: str, ja: list):
    """Escolhe comandos pelas pistas da frase. Tipos competem entre si (maior placar)."""
    placar = {k: len(rx.findall(texto)) for k, rx in _PISTAS.items()}
    achados = []
    tipos = [(placar[k], k) for k in _TIPOS if placar[k] > 0 and k not in ja
             and not any(t in ja for t in _TIPOS)]
    if tipos:
        achados.append(max(tipos)[1])       # empate: o primeiro na ordem de _TIPOS
    resto = sorted((k for k in CATALOGO if k not in _TIPOS and placar[k] > 0 and k not in ja),
                   key=lambda k: -placar[k])
    achados += resto
    return achados[:max(0, MAX_COMANDOS - len(ja))]


def analisar(texto: str) -> dict:
    """{'comandos': [...], 'explicitos': [...], 'automaticos': [...], 'texto': <limpo>,
        'tipo': 'codigo'|'video'|'imagem'|None, 'instrucao': str, 'mostrar': '/a /b'}"""
    original = (texto or "").strip()
    exp, limpo = _explicitos(original)
    auto = [] if exp else _automaticos(limpo, exp)   # digitou comando: ele manda
    todos = exp + [a for a in auto if a not in exp]
    todos = todos[:MAX_COMANDOS] if not exp else todos
    tipo = next((CATALOGO[c][0] for c in todos if CATALOGO[c][0]), None)
    return {
        "comandos": todos, "explicitos": exp, "automaticos": auto,
        "texto": limpo, "tipo": tipo,
        "instrucao": " ".join(CATALOGO[c][1] for c in todos),
        "mostrar": " ".join("/" + c for c in todos),
    }


def catalogo() -> list:
    return [{"comando": "/" + k, "tipo": v[0], "instrucao": v[1]} for k, v in CATALOGO.items()]
