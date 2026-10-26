"""Cofre de moedas (no servidor, em SQLite). Sem login: a conta é um código aleatório
que o app cria e guarda no celular (cabeçalho X-Conta).

Regras:
- Todo dia a conta ganha COFRE_DIARIO moedas GRÁTIS. O que não for usado NÃO se perde:
  acumula no cofre (limite de segurança COFRE_MAX, padrão 30 dias de moedas).
- Moedas COMPRADAS (crédito manual/pagamento) ficam à parte e nunca expiram nem têm limite.
- 1 geração de vídeo = CUSTO_VIDEO moedas; o Cineasta gasta 1 geração por cena.
- Gasta primeiro as grátis, depois as compradas. Falhou? Devolve exatamente o que cobrou.
- Sem o cabeçalho X-Conta nada é cobrado (apps antigos continuam funcionando).
"""
import os
import re
import sqlite3
import tempfile
import threading
import time

CUSTO_VIDEO = int(os.environ.get("COFRE_CUSTO_VIDEO", "10"))
DIARIO = int(os.environ.get("COFRE_DIARIO", "100"))
MAXIMO = int(os.environ.get("COFRE_MAX", str(DIARIO * 30)))
FUSO_H = float(os.environ.get("COFRE_FUSO_HORAS", "-3"))        # o "dia" vira à meia-noite do Brasil
_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def conta_valida(cid) -> bool:
    return bool(cid) and bool(_ID.match(cid))


class Cofre:
    def __init__(self, path=None):
        self.path = path or os.environ.get("DB_PATH") or os.path.join(
            tempfile.gettempdir(), "ttoken_cofre.db")
        self._lock = threading.Lock()
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS contas ("
                      "id TEXT PRIMARY KEY, gratis INTEGER, comprado INTEGER, "
                      "dia INTEGER, criada REAL)")

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _hoje(agora=None):
        return int(((agora if agora is not None else time.time()) + FUSO_H * 3600) // 86400)

    def _carregar(self, c, cid, agora=None):
        """Lê a conta (cria se for nova) e deposita os dias que passaram. Chamar com o lock."""
        hoje = self._hoje(agora)
        r = c.execute("SELECT * FROM contas WHERE id=?", (cid,)).fetchone()
        if not r:
            c.execute("INSERT INTO contas VALUES (?,?,?,?,?)",
                      (cid, min(DIARIO, MAXIMO), 0, hoje, time.time()))
            return {"gratis": min(DIARIO, MAXIMO), "comprado": 0, "dia": hoje}
        gratis, dia = r["gratis"], r["dia"]
        if hoje > dia:
            gratis = min(MAXIMO, gratis + (hoje - dia) * DIARIO)
            c.execute("UPDATE contas SET gratis=?, dia=? WHERE id=?", (gratis, hoje, cid))
        return {"gratis": gratis, "comprado": r["comprado"], "dia": hoje}

    def saldo(self, cid, agora=None) -> dict:
        with self._lock, self._conn() as c:
            s = self._carregar(c, cid, agora)
        return {"gratis": s["gratis"], "comprado": s["comprado"],
                "total": s["gratis"] + s["comprado"], "custo_video": CUSTO_VIDEO,
                "videos": (s["gratis"] + s["comprado"]) // CUSTO_VIDEO,
                "diario": DIARIO, "maximo_gratis": MAXIMO}

    def gastar(self, cid, n, agora=None):
        """Devolve (gratis_gasto, comprado_gasto) ou None se não houver saldo."""
        with self._lock, self._conn() as c:
            s = self._carregar(c, cid, agora)
            if s["gratis"] + s["comprado"] < n:
                return None
            g = min(s["gratis"], n)
            p = n - g
            c.execute("UPDATE contas SET gratis=gratis-?, comprado=comprado-? WHERE id=?",
                      (g, p, cid))
            return g, p

    def devolver(self, cid, gasto):
        """Devolve o que foi cobrado (tupla de gastar). As grátis respeitam o limite do cofre."""
        g, p = gasto
        with self._lock, self._conn() as c:
            self._carregar(c, cid)
            c.execute("UPDATE contas SET gratis=MIN(?, gratis+?), comprado=comprado+? WHERE id=?",
                      (MAXIMO, g, p, cid))

    def creditar(self, cid, n):
        """Moedas compradas/manuais: sem limite e sem validade."""
        if n <= 0:
            raise ValueError("quantidade deve ser positiva")
        with self._lock, self._conn() as c:
            self._carregar(c, cid)
            c.execute("UPDATE contas SET comprado=comprado+? WHERE id=?", (n, cid))
