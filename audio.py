"""Áudio -> texto com Whisper no Groq (grátis na sua chave GROQ). Usado pelo servidor
e pelo app (modo sem servidor)."""
import requests

from maestro import _keys_for

URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MODELO = "whisper-large-v3-turbo"


class SemTranscricao(Exception):
    pass


def transcrever(dados: bytes, nome="audio.m4a", chaves=None):
    chaves = chaves if chaves is not None else _keys_for("groq")
    if not chaves:
        raise SemTranscricao("Transcrição precisa de uma chave GROQ (GROQ_API_KEY).")
    if len(dados) > 24 * 1024 * 1024:
        raise SemTranscricao("Áudio grande demais (máx. ~24 MB).")
    ultimo = ""
    for k in chaves:
        try:
            r = requests.post(URL, headers={"Authorization": f"Bearer {k}"},
                              files={"file": (nome, dados)},
                              data={"model": MODELO, "language": "pt",
                                    "response_format": "json"}, timeout=90)
        except requests.RequestException as e:
            ultimo = str(e)[:100]
            continue
        if r.status_code == 200:
            return (r.json().get("text") or "").strip()
        ultimo = f"HTTP {r.status_code}"
    raise SemTranscricao(f"Groq não transcreveu ({ultimo}).")
