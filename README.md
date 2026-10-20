# Ttoken Studio — backend

Ideia → **Ttokem** (comprime só se compensar) → **Maestro de IAs** (roteiro, legenda,
hashtags, prompt de vídeo) → **Maestro de vídeo** → resultado. O usuário posta manualmente.

## Variáveis de ambiente (SnapDeploy)

Configure só o que tiver; quanto mais chaves grátis, mais crédito antes de acabar.

| Variável | Para quê |
|---|---|
| `GROQ_API_KEY` ou `GROQ_API_KEYS=k1,k2` | IA de texto (grátis) |
| `GEMINI_API_KEY` (ou o antigo `GEMINI_KEY`) | IA de texto (grátis) |
| `OPENROUTER_API_KEY`, `CEREBRAS_API_KEY`, `MISTRAL_API_KEY` | mais IAs de texto |
| `LLM_ORDER=groq,gemini,openrouter` | ordem de preferência |
| `<PROVEDOR>_MODEL` | troca o modelo se o provedor renomear |
| `VIDEO_PROVIDERS=https://proxy1/v1,https://proxy2/v1\|modelo\|chave` | geradores de vídeo, em ordem |
| `PROXY_URL` | gerador de vídeo antigo (entra no fim da lista) |

Quando uma chave/provedor devolve 429/402/quota, ele entra em cooldown e o próximo assume
automaticamente (texto e vídeo). Se todos os vídeos falharem, a legenda ainda é entregue.

## Endpoints

- `POST /api/gerar` `{"ideia": "..."}` → `{"success": true, "job_id": "..."}`
- `GET /api/status/{job_id}` → `status` (`processando` | `pronto` | `erro`), `texto`, `video_url`, `api_llm`, `api_video`, `tokens`
- `POST /api/comprimir` `{"texto": "...", "modo": "auto|light|v24"}` → testa o Ttokem
- `GET /health` → provedores configurados e em cooldown (sem expor chaves)

## Rodar / testar

```
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
python -m unittest discover -s tests -v
```

## Sobre o Ttokem

`ttokem.py` é uma versão **local** do prompt v24: não gasta chamada de IA. Só comprime textos
longos (≥ 600 caracteres) e só se economizar ≥ 10%; ideias curtas passam intactas. O formato
codebook (`§ID`) é para IAs de texto; **prompts de vídeo nunca usam codebook**.
