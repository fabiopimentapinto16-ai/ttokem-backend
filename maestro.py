"""Maestro: roteia chamadas entre vários provedores gratuitos.

Quando um provedor/chave estoura o crédito grátis (429/402/quota), ele entra
em "cooldown" e o próximo assume, sem o usuário perceber. Cada chave de cada
provedor é um "slot" independente: várias chaves = mais crédito grátis.

Variáveis de ambiente (todas opcionais; só os provedores com chave entram):
  GROQ_API_KEY / GROQ_API_KEYS (separadas por vírgula)      GROQ_MODEL
  GEMINI_API_KEY / GEMINI_KEY / GEMINI_API_KEYS             GEMINI_MODEL
  OPENROUTER_API_KEY / OPENROUTER_API_KEYS                  OPENROUTER_MODEL
  CEREBRAS_API_KEY / CEREBRAS_API_KEYS                      CEREBRAS_MODEL
  MISTRAL_API_KEY / MISTRAL_API_KEYS                        MISTRAL_MODEL
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
                   "meta-llama/llama-3.3-70b-instruct:free"),
    "cerebras": ("https://api.cerebras.ai/v1", "llama-3.3-70b"),
    "mistral": ("https://api.mistral.ai/v1", "mistral-small-latest"),
}
LEGACY_KEYS = {"gemini": ["GEMINI_KEY"]}


class AllProvidersFailed(Exception):
    pass


def _keys_for(name: str):
    up = name.upper()
    raw = []
    for var in [f"{up}_API_KEYS", f"{up}_API_KEY", *LEGACY_KEYS.get(name, [])]:
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
        self._lock = threading.Lock()

    # ----------------------------------------------------------- slots
    def slots(self):
        order = [n.strip() for n in os.environ.get("LLM_ORDER", "").split(",")
                 if n.strip() in PROVIDERS]
        order += [n for n in PROVIDERS if n not in order]
        out = []
        for name in order:
            base, default_model = PROVIDERS[name]
            model = os.environ.get(f"{name.upper()}_MODEL", default_model)
            for i, key in enumerate(_keys_for(name)):
                out.append({"id": f"{name}#{i + 1}", "name": name,
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
                        "disponivel": wait == 0, "volta_em_s": wait})
        return res

    # ------------------------------------------------------------ chamada
    def chat(self, messages, max_tokens=900, temperature=0.7):
        """Retorna (texto, 'provedor/modelo'). Levanta AllProvidersFailed."""
        slots = self.slots()
        if not slots:
            raise AllProvidersFailed(
                "Nenhuma chave de IA configurada (GROQ_API_KEY, GEMINI_API_KEY, ...)")
        errors = []
        for s in slots:
            if self._cooling(s["id"]) > 0:
                errors.append(f"{s['id']}: em cooldown")
                continue
            try:
                r = requests.post(
                    f"{s['base']}/chat/completions",
                    headers={"Authorization": f"Bearer {s['key']}"},
                    json={"model": s["model"], "messages": messages,
                          "max_tokens": max_tokens, "temperature": temperature},
                    timeout=self.timeout)
            except requests.RequestException as e:
                self._penalize(s["id"], 60)
                errors.append(f"{s['id']}: rede ({type(e).__name__})")
                continue

            if r.status_code == 200:
                try:
                    text = r.json()["choices"][0]["message"]["content"]
                except (KeyError, IndexError, ValueError):
                    self._penalize(s["id"], 60)
                    errors.append(f"{s['id']}: resposta inválida")
                    continue
                if text and text.strip():
                    return text.strip(), f"{s['name']}/{s['model']}"
                errors.append(f"{s['id']}: resposta vazia")
                continue

            body = (r.text or "")[:300].lower()
            if r.status_code == 429:
                retry = r.headers.get("Retry-After", "")
                wait = int(retry) if retry.isdigit() else (
                    3600 if "quota" in body or "daily" in body else 90)
            elif r.status_code in (402, 403) or "quota" in body or "credit" in body:
                wait = 3600
            elif r.status_code in (401, 404):
                wait = 6 * 3600     # chave/modelo inválido: não insistir
            else:
                wait = 60
            self._penalize(s["id"], wait)
            errors.append(f"{s['id']}: HTTP {r.status_code}")
        raise AllProvidersFailed("; ".join(errors))
