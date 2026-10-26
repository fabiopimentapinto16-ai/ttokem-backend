"""Maestro: roteia chamadas entre vários provedores gratuitos.

Quando um provedor/chave estoura o crédito grátis (429/402/quota), ele entra
em "cooldown" e o próximo assume, sem o usuário perceber. Cada chave de cada
provedor é um "slot" independente: várias chaves = mais crédito grátis.

Variáveis de ambiente (todas opcionais; só os provedores com chave entram):
  GROQ_API_KEY / GROQ_API_KEYS (separadas por vírgula)      GROQ_MODEL(S)
  GEMINI_API_KEY / GEMINI_KEY / GEMINI_API_KEYS             GEMINI_MODEL
  HF_TOKEN (Hugging Face)                                   HUGGINGFACE_MODEL
  OPENROUTER_API_KEY / OPENROUTER_API_KEYS                  OPENROUTER_MODEL
  CEREBRAS_API_KEY / CEREBRAS_API_KEYS                      CEREBRAS_MODEL
  MISTRAL_API_KEY / MISTRAL_API_KEYS                        MISTRAL_MODEL
  <PROVEDOR>_MODELS=modelo1,modelo2  (cada modelo vira um slot próprio; no
  OpenRouter uma única chave dá acesso a vários modelos, inclusive os ":free")
  LLM_ORDER=groq,gemini,openrouter,...   (ordem de preferência)
Todos falam o protocolo OpenAI /chat/completions.
"""
import os
import re
import threading
import time

import requests

# nome: (base_url, modelo padrão — troque por env se o provedor mudar o nome)
PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "openai/gpt-oss-120b"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai",
               "gemini-3.1-flash-lite,gemini-3-flash-preview,gemini-flash-latest"),
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter/free"),
    "cerebras": ("https://api.cerebras.ai/v1", "llama-3.3-70b"),
    "mistral": ("https://api.mistral.ai/v1", "mistral-small-latest"),
    # Beam (Reflection AI, open-weight 501B MoE): API compatível com OpenAI. Só entra com chave.
    "reflection": ("https://api.reflection.ai/openai/v1", "Beam-1.0-501B-A23B"),
    # Hugging Face Inference Providers (HF_TOKEN): créditos mensais pequenos, fica por último
    "huggingface": ("https://router.huggingface.co/v1", "meta-llama/Llama-3.3-70B-Instruct"),
}


def provedores():
    """PROVIDERS + 'custom' (qualquer API compatível com OpenAI: Beam, FreeLLMAPI, Ollama...).
    Ligue com CUSTOM_BASE_URL, CUSTOM_API_KEY e (opcional) CUSTOM_MODEL."""
    out = dict(PROVIDERS)
    base = os.environ.get("CUSTOM_BASE_URL", "").strip().rstrip("/")
    if base:
        out["custom"] = (base, os.environ.get("CUSTOM_MODEL", "").strip() or "default")
    return out


LEGACY_KEYS = {"gemini": ["GOOGLE_API_KEY"],
               "huggingface": ["HF_TOKEN", "HF_API_KEY", "HUGGINGFACE_TOKEN"]}


# Provedores desligam modelos o tempo todo: em vez de confiar em nomes fixos, o maestro
# pergunta ao provedor quais modelos existem (GET /models) e escolhe os melhores.
_EXCLUI = ("whisper", "guard", "safeguard", "tts", "orpheus", "compound", "embed", "playai",
           "image", "audio", "live", "robotics", "moderation", "vision-preview",
           "inkling")      # exigem "agentic harness": dão HTTP 403 em chat comum
_PREF = {
    "groq": ["gpt-oss-120b", "qwen3.8", "qwen3.6", "gpt-oss-20b", "llama-3.3", "llama-4", "llama-3.1"],
    "gemini": ["3.1-flash-lite", "3-flash", "flash-latest", "2.5-flash-lite", "2.5-flash", "flash"],
}


def _rank(name, ids):
    pref = _PREF.get(name, [])
    ids = [i for i in ids if not any(x in i.lower() for x in _EXCLUI)]
    if name == "gemini":
        ids = [i for i in ids if i.startswith("gemini-") and "-pro" not in i and "exp" not in i]

    def pos(i):
        for n, t in enumerate(pref):
            if t in i:
                return n
        return 99
    return sorted(ids, key=lambda i: (pos(i), i))


class AllProvidersFailed(Exception):
    pass


def _keys_for(name: str):
    up = name.upper()
    raw = []
    for var in [f"{up}_API_KEYS", f"{up}_API_KEY", f"{up}_KEY", f"{up}_TOKEN",
                f"{up}_API_TOKEN", *LEGACY_KEYS.get(name, [])]:
        raw += [k.strip() for k in os.environ.get(var, "").split(",")]
    seen, keys = set(), []
    for k in raw:
        if k and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


class Maestro:
    def __init__(self, timeout: int = 25):
        self.timeout = timeout
        self._cool = {}          # slot -> timestamp de liberação
        self._last = {}          # slot -> último erro (mostrado ao usuário)
        self._disc = {}          # provedor -> (expira, [modelos descobertos])
        self._lock = threading.Lock()

    # ----------------------------------------------------------- slots
    def slots(self):
        prov = provedores()
        order = [n.strip() for n in os.environ.get("LLM_ORDER", "").split(",")
                 if n.strip() in prov]
        order += [n for n in prov if n not in order]
        out = []
        for name in order:
            base, default_model = prov[name]
            raw = (os.environ.get(f"{name.upper()}_MODELS")
                   or os.environ.get(f"{name.upper()}_MODEL")
                   or ",".join(self._disc.get(name, (0, []))[1]) or default_model)
            models = [m.strip() for m in raw.split(",") if m.strip()]
            for i, key in enumerate(_keys_for(name)):
                for model in models:      # 1 chave + N modelos = N slots
                    out.append({"id": f"{name}#{i + 1}:{model}", "name": name,
                                "base": base, "model": model, "key": key})
        return out

    def _descobrir(self, name, base, key):
        """Pergunta ao provedor quais modelos existem agora (cache de 6 h)."""
        if os.environ.get(f"{name.upper()}_MODELS") or os.environ.get(f"{name.upper()}_MODEL"):
            return                                  # o usuário fixou o modelo
        if name not in ("groq", "gemini", "openrouter"):
            return
        exp, _ = self._disc.get(name, (0, []))
        if exp > time.time():
            return
        try:
            r = requests.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"},
                             timeout=10)
            itens = r.json().get("data", []) if r.status_code == 200 else []
            if name == "openrouter":
                livres = []
                for m in itens:
                    pr = m.get("pricing") or {}
                    if str(pr.get("prompt")) == "0" and str(pr.get("completion")) == "0" \
                            and not any(x in m["id"].lower() for x in _EXCLUI):
                        livres.append((m.get("context_length") or 0, m["id"]))
                ids = ["openrouter/free"] + [i for _, i in sorted(livres, reverse=True)][:2]
            else:
                ids = _rank(name, [m["id"].replace("models/", "") for m in itens if "id" in m])[:2]
            self._disc[name] = (time.time() + (6 * 3600 if ids else 300), ids)
        except Exception:
            self._disc[name] = (time.time() + 300, [])

    def _descobrir_todos(self):
        for name, (base, _m) in PROVIDERS.items():
            keys = _keys_for(name)
            if keys:
                self._descobrir(name, base, keys[0])

    def _cooling(self, slot_id):
        with self._lock:
            return self._cool.get(slot_id, 0) - time.time()

    def _penalize(self, slot_id, seconds):
        with self._lock:
            self._cool[slot_id] = time.time() + seconds

    def status(self):
        res = []
        for s in self.slots():
            wait = max(0, round(self._cooling(s["id"])))
            res.append({"slot": s["id"], "model": s["model"],
                        "disponivel": wait == 0, "volta_em_s": wait,
                        "ultimo_erro": self._last.get(s["id"], "")})
        return res

    # ------------------------------------------------------------ chamada
    @staticmethod
    def _motivo(r):
        """Texto curto do erro do provedor (para o usuário saber o que consertar)."""
        try:
            j = r.json()
            e = j.get("error", j) if isinstance(j, dict) else j
            if isinstance(e, list) and e:
                e = e[0].get("error", e[0]) if isinstance(e[0], dict) else e[0]
            msg = e.get("message") if isinstance(e, dict) else str(e)
        except Exception:
            msg = r.text
        return f"HTTP {r.status_code} ({str(msg or '').strip()[:90]})"

    def _tentar(self, s, messages, max_tokens, temperature):
        """Uma chamada. Retorna (texto|None, erro|None) e já aplica o cooldown."""
        try:
            r = requests.post(
                f"{s['base']}/chat/completions",
                headers={"Authorization": f"Bearer {s['key']}"},
                json={"model": s["model"], "messages": messages,
                      "max_tokens": max_tokens, "temperature": temperature},
                timeout=self.timeout)
        except requests.RequestException as e:
            self._penalize(s["id"], 60)
            return None, f"rede ({type(e).__name__}: {str(e)[:60]})"
        if r.status_code == 200:
            try:
                text = r.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError, TypeError):
                self._penalize(s["id"], 60)
                return None, "resposta inválida"
            if text and text.strip():
                self._last.pop(s["id"], None)
                self._penalize(s["id"], 0)
                return text.strip(), None
            return None, "resposta vazia"
        body = (r.text or "")[:300].lower()
        if r.status_code == 429:
            retry = r.headers.get("Retry-After", "")
            wait = int(retry) if retry.isdigit() else (
                3600 if "quota" in body or "daily" in body else 90)
        elif r.status_code in (402, 403) or "quota" in body or "credit" in body:
            wait = 1800
        elif r.status_code in (401, 404):
            wait = 600          # chave/modelo inválido: espera pouco (você pode corrigir)
        else:
            wait = 60
        self._penalize(s["id"], min(wait, 3600))
        if r.status_code == 404:
            self._disc.pop(s["name"], None)          # modelo sumiu: redescobrir na próxima
        return None, self._motivo(r)

    def chat(self, messages, max_tokens=900, temperature=0.7):
        """Retorna (texto, 'provedor/modelo'). Levanta AllProvidersFailed."""
        self._descobrir_todos()
        slots = self.slots()
        if not slots:
            raise AllProvidersFailed(
                "Nenhuma chave de IA configurada (GROQ_API_KEY, GEMINI_API_KEY, ...)")
        livres = [s for s in slots if self._cooling(s["id"]) <= 0]
        # Se TODOS estão esfriando, tenta mesmo assim (o mais próximo de voltar
        # primeiro): evita ficar preso sem nunca testar de novo.
        fila = livres or sorted(slots, key=lambda x: self._cooling(x["id"]))
        errors = []
        for s in fila:
            texto, erro = self._tentar(s, messages, max_tokens, temperature)
            if texto:
                return texto, f"{s['name']}/{s['model']}"
            self._last[s["id"]] = erro
            errors.append(f"{s['id']}: {erro}")
        raise AllProvidersFailed("; ".join(errors))

    def arena(self, messages, n=3, max_tokens=900):
        """Melhor de N: IAs de provedores DIFERENTES respondem a mesma tarefa e uma delas
        julga. Gasta ~N vezes a cota. Retorna (texto, 'arena N: provedor/modelo', detalhes).
        Se só uma IA responder, devolve essa; se o juiz falhar, vence a resposta mais completa."""
        from concurrent.futures import ThreadPoolExecutor
        n = max(2, min(int(n or 3), 5))
        self._descobrir_todos()
        slots = [s for s in self.slots() if self._cooling(s["id"]) <= 0]
        escolhidos, vistos = [], set()
        for s in slots:                                  # 1 slot por provedor
            if s["name"] not in vistos:
                vistos.add(s["name"])
                escolhidos.append(s)
            if len(escolhidos) == n:
                break
        if len(escolhidos) < 2:
            texto, ia = self.chat(messages, max_tokens=max_tokens)
            return texto, ia, {"candidatas": 1, "juiz": "n/a (so 1 IA livre)"}
        with ThreadPoolExecutor(max_workers=len(escolhidos)) as pool:
            res = list(pool.map(lambda s: (s, *self._tentar(s, messages, max_tokens, 0.7)),
                                escolhidos))
        ok = [(s, t) for s, t, e in res if t]
        for s, t, e in res:
            if e:
                self._last[s["id"]] = e
        if not ok:
            raise AllProvidersFailed("; ".join(f"{s['id']}: {e}" for s, t, e in res))
        if len(ok) == 1:
            s, t = ok[0]
            return t, f"{s['name']}/{s['model']}", {"candidatas": 1, "juiz": "n/a"}
        pergunta = next((x["content"] for x in reversed(messages) if x.get("role") == "user"), "")
        letras = "ABCDE"[:len(ok)]
        bloco = "\n\n".join(f"[{letras[i]}]\n{t[:1500]}" for i, (s, t) in enumerate(ok))
        juiz_msgs = [{"role": "system", "content": "Voce e um juiz imparcial. Escolha a melhor resposta "
                      "(precisa, completa, clara, util). Responda APENAS com a letra."},
                     {"role": "user", "content": f"TAREFA:\n{pergunta[:1500]}\n\nRESPOSTAS:\n{bloco}"}]
        idx, juiz = None, "fallback (a mais completa)"
        try:
            voto, juiz_ia = self.chat(juiz_msgs, max_tokens=5, temperature=0)
            mt = re.search(r"[A-E]", voto.upper())
            if mt and mt.group(0) in letras:
                idx, juiz = letras.index(mt.group(0)), juiz_ia
        except AllProvidersFailed:
            pass
        if idx is None:
            idx = max(range(len(ok)), key=lambda i: len(ok[i][1]))
        s, t = ok[idx]
        return t, f"arena {len(ok)}: {s['name']}/{s['model']}", {"candidatas": len(ok), "juiz": juiz}

    def testar(self):
        """Faz 1 chamada mínima por slot (ignora cooldown). Para o botão 'Testar chaves'."""
        out = []
        self._disc.clear()
        self._descobrir_todos()
        for s in self.slots():
            texto, erro = self._tentar(
                s, [{"role": "user", "content": "Responda apenas: ok"}], 8, 0)
            if erro:
                self._last[s["id"]] = erro
            out.append({"slot": s["id"], "provedor": s["name"], "ok": bool(texto),
                        "erro": erro or ""})
        return out
