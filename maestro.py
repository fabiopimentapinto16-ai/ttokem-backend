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
import threading
import time

import requests

# nome: (base_url, modelo padrão — troque por env se o provedor mudar o nome)
PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai",
               "gemini-2.5-flash"),
    "openrouter": ("https://openrouter.ai/api/v1",
                   "meta-llama/llama-3.3-70b-instruct:free,"
                   "google/gemma-3-27b-it:free"),
    "cerebras": ("https://api.cerebras.ai/v1", "llama-3.3-70b"),
    "mistral": ("https://api.mistral.ai/v1", "mistral-small-latest"),
    # Hugging Face Inference Providers (HF_TOKEN): créditos mensais pequenos, fica por último
    "huggingface": ("https://router.huggingface.co/v1", "meta-llama/Llama-3.3-70B-Instruct"),
}
LEGACY_KEYS = {"gemini": ["GOOGLE_API_KEY"],
               "huggingface": ["HF_TOKEN", "HF_API_KEY", "HUGGINGFACE_TOKEN"]}


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
        self._lock = threading.Lock()

    # ----------------------------------------------------------- slots
    def slots(self):
        order = [n.strip() for n in os.environ.get("LLM_ORDER", "").split(",")
                 if n.strip() in PROVIDERS]
        order += [n for n in PROVIDERS if n not in order]
        out = []
        for name in order:
            base, default_model = PROVIDERS[name]
            raw = (os.environ.get(f"{name.upper()}_MODELS")
                   or os.environ.get(f"{name.upper()}_MODEL") or default_model)
            models = [m.strip() for m in raw.split(",") if m.strip()]
            for i, key in enumerate(_keys_for(name)):
                for model in models:      # 1 chave + N modelos = N slots
                    out.append({"id": f"{name}#{i + 1}:{model}", "name": name,
                                "base": base, "model": model, "key": key})
        return out

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
        return None, self._motivo(r)

    def chat(self, messages, max_tokens=900, temperature=0.7):
        """Retorna (texto, 'provedor/modelo'). Levanta AllProvidersFailed."""
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

    def testar(self):
        """Faz 1 chamada mínima por slot (ignora cooldown). Para o botão 'Testar chaves'."""
        out = []
        for s in self.slots():
            texto, erro = self._tentar(
                s, [{"role": "user", "content": "Responda apenas: ok"}], 8, 0)
            if erro:
                self._last[s["id"]] = erro
            out.append({"slot": s["id"], "provedor": s["name"], "ok": bool(texto),
                        "erro": erro or ""})
        return out
