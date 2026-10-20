"""Maestro de vídeo: tenta cada gerador na ordem; se um falhar, passa ao próximo.

Cada gerador é um endpoint no formato "job assíncrono" já usado pelo seu proxy:
  POST {base}/videos            {"model", "prompt"}  -> {"id"}
  GET  {base}/videos/{id}       -> {"status": "completed"|"failed"|...}
  GET  {base}/videos/{id}/content  -> o vídeo

Configuração (ordem = prioridade):
  VIDEO_PROVIDERS = "base1|modelo|chave, base2|modelo|chave, ..."
     (modelo e chave são opcionais: "https://meu-proxy/v1" basta)
  PROXY_URL (legado, entra no fim da lista)  VIDEO_MODEL (padrão wan-fast)
"""
import os
import threading
import time

import requests


class VideoFailed(Exception):
    pass


def _providers():
    default_model = os.environ.get("VIDEO_MODEL", "wan-fast")
    entries = [e.strip() for e in os.environ.get("VIDEO_PROVIDERS", "").split(",")
               if e.strip()]
    legacy = os.environ.get("PROXY_URL", "").strip()
    if legacy and not any(e.split("|")[0] == legacy for e in entries):
        entries.append(legacy)
    out = []
    for e in entries:
        parts = [p.strip() for p in e.split("|")]
        base = parts[0].rstrip("/")
        out.append({"id": base,
                    "base": base,
                    "model": parts[1] if len(parts) > 1 and parts[1] else default_model,
                    "key": parts[2] if len(parts) > 2 else ""})
    return out


class VideoMaestro:
    def __init__(self, poll_every=5, max_wait=240):
        self.poll_every, self.max_wait = poll_every, max_wait
        self._cool, self._lock = {}, threading.Lock()

    def status(self):
        now = time.time()
        return [{"gerador": p["id"], "modelo": p["model"],
                 "disponivel": self._cool.get(p["id"], 0) <= now}
                for p in _providers()]

    def _penalize(self, pid, seconds):
        with self._lock:
            self._cool[pid] = time.time() + seconds

    def generate(self, prompt: str):
        """Retorna (video_url, gerador). Levanta VideoFailed."""
        provs = _providers()
        if not provs:
            raise VideoFailed("Nenhum gerador de vídeo configurado (VIDEO_PROVIDERS)")
        errors = []
        for p in provs:
            if self._cool.get(p["id"], 0) > time.time():
                errors.append(f"{p['id']}: em cooldown")
                continue
            hdr = {"Authorization": f"Bearer {p['key']}"} if p["key"] else {}
            try:
                r = requests.post(f"{p['base']}/videos", headers=hdr, timeout=30,
                                  json={"model": p["model"], "prompt": prompt})
                if r.status_code in (402, 429):      # crédito acabou
                    self._penalize(p["id"], 3600)
                    errors.append(f"{p['id']}: sem crédito (HTTP {r.status_code})")
                    continue
                r.raise_for_status()
                job = r.json()["id"]
                deadline = time.time() + self.max_wait
                while time.time() < deadline:
                    st = requests.get(f"{p['base']}/videos/{job}", headers=hdr,
                                      timeout=20).json()
                    s = st.get("status")
                    if s == "completed":
                        return f"{p['base']}/videos/{job}/content", p["id"]
                    if s == "failed":
                        raise VideoFailed(st.get("error") or "falhou")
                    time.sleep(self.poll_every)
                raise VideoFailed("tempo esgotado")
            except (requests.RequestException, KeyError, ValueError, VideoFailed) as e:
                self._penalize(p["id"], 120)
                errors.append(f"{p['id']}: {e}")
        raise VideoFailed("; ".join(errors))
