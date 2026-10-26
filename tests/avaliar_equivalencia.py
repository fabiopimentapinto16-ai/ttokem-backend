"""Mede se a IA responde IGUAL com o prompt original e com o comprimido.

Uso (no Colab ou onde houver chave de IA de texto: GROQ_API_KEY, GEMINI_API_KEY...):
    python tests/avaliar_equivalencia.py [quantidade] [modo]
Para cada prompt do benchmark: responde o original e o comprimido e pede a uma 3ª chamada
(juiz) para dizer se as duas respostas entregam a MESMA informação/ação. Imprime economia
de tokens e % de equivalência. Usa tiktoken se existir. Sem chave de IA, avisa e sai.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ttokem                      # noqa: E402
from maestro import Maestro        # noqa: E402

try:
    import tiktoken
    _e = tiktoken.get_encoding("cl100k_base")
    tok = lambda s: len(_e.encode(s))          # noqa: E731
    TOKENIZER = "tiktoken cl100k_base"
except Exception:
    tok = lambda s: max(1, round(len(re.findall(r"\w+|[^\w\s]", s)) * 1.3))   # noqa: E731
    TOKENIZER = "aproximado"

JUIZ = ("Compare as duas respostas ao mesmo pedido. Elas entregam a MESMA informação, dados, "
        "decisões e ação (diferença só de estilo é ok)? Responda SOMENTE 'SIM' ou 'NAO'.\n\n"
        "RESPOSTA A:\n{a}\n\nRESPOSTA B:\n{b}")


def main():
    import benchmark_concorrentes as bc
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    modo = sys.argv[2] if len(sys.argv) > 2 else "agressivo"
    llm = Maestro()
    if not llm.status():
        print("Sem chave de IA de texto configurada. Defina GROQ_API_KEY ou GEMINI_API_KEY.")
        return 1
    ok = tot = gasto0 = gasto1 = 0
    for p in bc.PROMPTS[:n]:
        c = ttokem.compress(p, modo)
        if c["text"] == p:
            continue                                       # sem compressão: nada a comparar
        try:
            a, _ = llm.chat([{"role": "user", "content": p}], max_tokens=500)
            b, _ = llm.chat([{"role": "user", "content": c["text"]}], max_tokens=500)
            v, _ = llm.chat([{"role": "user", "content": JUIZ.format(a=a[:1500], b=b[:1500])}],
                            max_tokens=5)
        except Exception as e:
            print("falha de IA:", e)
            continue
        tot += 1
        ok += v.strip().upper().startswith("SIM")
        gasto0, gasto1 = gasto0 + tok(p), gasto1 + tok(c["text"])
    if not tot:
        print("nenhum prompt avaliado")
        return 1
    print(f"tokenizer: {TOKENIZER} | modo: {modo} | prompts comprimidos avaliados: {tot}")
    print(f"economia de tokens: {(1 - gasto1 / gasto0) * 100:.1f}%")
    print(f"respostas equivalentes (juiz IA): {ok}/{tot} = {ok / tot * 100:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
