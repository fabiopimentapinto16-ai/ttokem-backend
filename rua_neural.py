"""Rua Neural v2: árvore hierárquica de modelos (NHHC-4F) com herança de restrições.

raiz -> contexto -> tarefa (imagem | video | codigo) -> modelo (folha)

* Cada nó tem `restricoes` (regex). O que um pai exige, o filho herda: se o ORIGINAL contém
  algo que a regra descreve, o texto FINAL (já expandido) tem que conter o mesmo.
* Seleção em sequência (nível a nível): o tipo é detectado, depois cada frase do pedido é
  comparada só com as frases dos modelos DAQUELE tipo. Em qualquer dúvida, não troca nada.
* Frase só é trocada se for praticamente idêntica (>= LIMITE de palavras iguais) E tiver
  exatamente a mesma lógica (não/nunca/ou/mas...) e os mesmos fatos (números, ids, e-mails).
* Estatística local (hits/co-ocorrência) só ordena sugestões; não altera o texto.
Determinístico, Python puro, sem IA e sem dependências.
"""
import json
import re
from collections import Counter

import ttokem

LIMITE = 0.95

RESUMOS = {   # L0: uma linha para escolher sem carregar o texto completo
    "foto_pro": "melhorar foto/retrato mantendo a mesma pessoa (luz, bokeh, cor de cinema)",
    "produto_cinema": "comercial cinematográfico de produto em 9:16, 5 s, sem texto",
    "retrato_corporativo": "retrato corporativo, fundo cinza claro, mesmo rosto e roupa",
    "app_kivy_apk": "app Kivy -> APK (buildozer + GitHub Actions), thread em segundo plano",
}

REGRAS = {   # herança: ROOT vale para todos; cada tarefa soma as suas
    "ROOT": [r"https?://\S+", r"[\w.+-]+@[\w-]+\.[\w.]+", r"#\w+", r"\b\w*\d\w*(?:[-.:/]\w+)*\b"],
    "imagem": [r"\b\d+mm\b", r"\bf/\d+(?:\.\d+)?\b", r"\b(?:no|sem|n[aã]o)\s+\w+"],
    "video": [r"\b\d{1,2}:\d{2}\b", r"\b\d+\s?(?:seconds?|segundos?|s|fps)\b",
              r"\b(?:no|sem)\s+(?:text|watermark|subtitles|dialogue|texto|legenda)\b"],
    "codigo": [r"[\w./-]+\.(?:py|yml|yaml|spec|json|xml|md)\b", r"\bubuntu-[\d.]+\b",
               r"\bpython ?\d+(?:\.\d+)?\b"],
}


class No:
    def __init__(self, nome, nivel, pai=None, resumo="", frases=None, regras=None):
        self.nome, self.nivel, self.pai = nome, nivel, pai
        self.resumo, self.frases, self.regras = resumo, frases or [], regras or []
        self.filhos, self.hits = [], 0
        if pai:
            pai.filhos.append(self)

    def regras_herdadas(self):
        acc, n = [], self
        while n:
            acc = n.regras + acc
            n = n.pai
        return acc


class Biblioteca:
    def __init__(self, presets=None):
        self.raiz = No("RAIZ", "ROOT", regras=REGRAS["ROOT"])
        ctx = No("CONTEXTO", "CONTEXT", self.raiz)
        self.tarefas = {t: No("TAREFA_" + t.upper(), "TASK", ctx, regras=REGRAS[t])
                        for t in ("imagem", "video", "codigo")}
        self.modelos = {}
        self.co = Counter()
        for pid, pre in (presets or ttokem.PRESETS).items():
            self.adicionar(pid, pre["tipo"], pre["frases"])

    def adicionar(self, pid, tipo, frases, resumo=""):
        no = No(pid, "MODEL", self.tarefas[tipo], resumo or RESUMOS.get(pid, pid), frases)
        self.modelos[pid] = no
        return no

    # ------------------------------------------------------------- JSON (biblioteca em arquivo)
    def exportar(self):
        return json.dumps({p: {"tipo": n.pai.nome[7:].lower(), "resumo": n.resumo,
                               "frases": n.frases} for p, n in self.modelos.items()},
                          ensure_ascii=False, indent=1)

    @classmethod
    def carregar(cls, caminho):
        dados = json.load(open(caminho, encoding="utf-8"))
        b = cls(presets={})
        for pid, d in dados.items():
            if d.get("tipo") in b.tarefas and d.get("frases"):
                b.adicionar(pid, d["tipo"], d["frases"], d.get("resumo", ""))
        return b

    # ------------------------------------------------------------------ seleção
    @staticmethod
    def _equivalente(a, b, limite):
        if ttokem._sim(a, b) < limite:
            return False
        ca = {w for w in re.findall(r"[\w'-]+", a.lower()) if w not in ttokem._STOP}
        cb = {w for w in re.findall(r"[\w'-]+", b.lower()) if w not in ttokem._STOP}
        if ca != cb:                         # nenhuma palavra de conteúdo a mais ou a menos
            return False
        la = Counter(m.lower() for m in ttokem._LOGIC.findall(a))
        lb = Counter(m.lower() for m in ttokem._LOGIC.findall(b))
        fa = {f.rstrip(".,;:)").lower() for f in ttokem._FACT.findall(a)}
        fb = {f.rstrip(".,;:)").lower() for f in ttokem._FACT.findall(b)}
        return la == lb and fa == fb

    def conferir(self, no, original, expandido):
        """Todas as regras herdadas: o que existe no original tem que existir igual no final."""
        for rx in no.regras_herdadas():
            o = Counter(m.lower() for m in re.findall(rx, original, re.IGNORECASE))
            e = Counter(m.lower() for m in re.findall(rx, expandido, re.IGNORECASE))
            if any(e[k] < v for k, v in o.items()):
                return False, rx
        return True, ""

    def selecionar(self, texto, tipo=None, limite=LIMITE):
        """Retorna (texto_com_referencias, nº de frases trocadas, tarefa|None)."""
        tipo = tipo if tipo in self.tarefas else ttokem.detectar_tipo(texto)
        if tipo not in self.tarefas:
            return texto, 0, None                       # sem tipo claro: não troca nada
        candidatos = [n for n in self.modelos.values() if n.pai is self.tarefas[tipo]]
        sents = ttokem._sentences(texto)
        saida, n, usados = [], 0, []
        for sn in sents:
            achou = None
            for no in candidatos:
                for k, fr in enumerate(no.frases, 1):
                    if self._equivalente(sn, fr, limite):
                        achou = (no, k)
                        break
                if achou:
                    break
            saida.append(achou if achou else sn)
            if achou:
                n += 1
                usados.append(achou)
        if not n:
            return texto, 0, tipo
        final, i = [], 0
        while i < len(saida):
            it = saida[i]
            if isinstance(it, tuple):
                no = it[0]; seq = []
                while i < len(saida) and isinstance(saida[i], tuple) and saida[i][0] is no:
                    seq.append(saida[i][1]); i += 1
                final.append(f"@{no.nome}" if seq == list(range(1, len(no.frases) + 1))
                             else " ".join(f"@{no.nome}.{k}" for k in seq))
            else:
                final.append(it); i += 1
        res = " ".join(final)
        exp = ttokem.expandir_presets(res)
        ok_sem = ttokem.semantica(texto, exp)["intacta"]
        ok_reg = all(self.conferir(no, texto, exp)[0] for no, _ in usados)
        if not (ok_sem and ok_reg):
            return texto, 0, tipo                       # fail-closed
        for no, _ in usados:
            no.hits += 1
        for a in {no.nome for no, _ in usados}:
            for b in {no.nome for no, _ in usados}:
                if a < b:
                    self.co[(a, b)] += 1                 # co-ocorrência (só estatística)
        return res, n, tipo


BIBLIOTECA = Biblioteca()
