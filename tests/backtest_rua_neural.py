"""Backtest: Rua Neural v2 (limite 95% + árvore) contra a versão antiga (limite 80%).
Mede economia e PERDA SILENCIOSA: palavras do pedido original que somem (ou palavras
inventadas que entram) depois que o modelo pronto é expandido."""
import os, re, sys, statistics
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ttokem, rua_neural

tok = lambda t: max(1, len(re.findall(r"\w+|[^\w\s]", t)))
conteudo = lambda t: {w for w in re.findall(r"[\w'-]+", t.lower()) if len(w) > 2 and w not in ttokem._STOP}

TROCAS = [("soft", "hard"), ("cinematic", "vintage"), ("golden", "silver"), ("natural", "artificial"),
          ("dark", "bright"), ("slow", "fast"), ("rich", "faded"), ("light gray", "dark green"),
          ("blue", "red"), ("glossy", "matte")]


def variantes(pid, pre):
    full = " ".join(pre["frases"])
    out = [("exato", full)]
    for a, b in TROCAS:
        if a in full.lower():
            out.append((f"troca '{a}'->'{b}'", re.sub(a, b, full, count=1, flags=re.I)))
    out.append(("detalhe novo", full + " Use a red background and add a small dog."))
    out.append(("remove palavra", re.sub(r"\b(?:soft|gentle|slow|natural|rich)\b ?", "", full, count=1, flags=re.I)))
    out.append(("numero muda", re.sub(r"\b(\d+) seconds", lambda m: f"{int(m.group(1)) + 3} seconds", full)))
    out.append(("logica muda", re.sub(r"\b(?:no|not) ", "", full, count=1)))
    return [(pid, n, t) for n, t in out if t != full or n == "exato"]


def v1(t):
    r, n = ttokem._aplicar_presets_v1(t, 0.80)
    return r, n


def v2(t):
    r, n = ttokem.aplicar_presets(t)
    return r, n


def rodar(imprimir=True):
    casos = [v for pid, pre in ttokem.PRESETS.items() for v in variantes(pid, pre)]
    res = {}
    for nome, fn in (("v1 (80%)", v1), ("v2 Rua Neural (95%)", v2)):
        eco, trocados, perdas, inventadas, detalhes = [], 0, 0, 0, []
        for pid, cenario, t in casos:
            ref, n = fn(t)
            exp = ttokem.expandir_presets(ref)
            if n:
                trocados += 1
                eco.append(100 * (tok(t) - tok(ref)) / tok(t))
            falt = conteudo(t) - conteudo(exp)
            inv = conteudo(exp) - conteudo(t)
            if falt:
                perdas += 1; detalhes.append((pid, cenario, "perdeu", sorted(falt)[:3]))
            if inv:
                inventadas += 1; detalhes.append((pid, cenario, "inventou", sorted(inv)[:3]))
        res[nome] = dict(casos=len(casos), trocados=trocados, eco=statistics.mean(eco) if eco else 0,
                         perdas=perdas, inventadas=inventadas, detalhes=detalhes)
    if imprimir:
        print(f"{'versao':<22}{'casos':>6}{'trocados':>9}{'econ. media(trocados)':>23}{'perda silenciosa':>18}{'palavras inventadas':>21}")
        for nome, r in res.items():
            print(f"{nome:<22}{r['casos']:>6}{r['trocados']:>9}{r['eco']:>22.1f}%{r['perdas']:>18}{r['inventadas']:>21}")
        for nome, r in res.items():
            if r["detalhes"]:
                print(f"\n[{nome}] exemplos de problema:")
                for d in r["detalhes"][:6]:
                    print("  ", d)
    return res


if __name__ == "__main__":
    rodar()
