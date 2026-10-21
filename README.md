# Ttoken Studio — backend

Ideia → **Ttokem** (comprime só se compensar) → **Maestro de IAs** (roteiro, legenda,
hashtags, prompt de vídeo) → **Maestro de vídeo** → resultado. O usuário posta manualmente.

## Variáveis de ambiente (SnapDeploy → Container Settings → Environment Variables)

Configure só o que tiver. Cada chave é um "slot": quando o crédito de um acaba (429/402),
ele esfria e o próximo assume sozinho.

### IAs de texto (precisa de pelo menos uma)
| Variável | Onde pegar a chave |
|---|---|
| `OPENROUTER_API_KEY` — **1 chave, vários modelos** (inclui `:free`) | https://openrouter.ai/keys |
| `GROQ_API_KEY` | https://console.groq.com/keys |
| `GEMINI_API_KEY` (ou o antigo `GEMINI_KEY`) | https://aistudio.google.com/apikey |
| `MISTRAL_API_KEY` | https://console.mistral.ai/api-keys |
| `CEREBRAS_API_KEY` | https://cloud.cerebras.ai |

Extras: `<PROVEDOR>_API_KEYS=k1,k2` (várias chaves), `<PROVEDOR>_MODELS=m1,m2` (vários
modelos), `LLM_ORDER=groq,gemini,openrouter` (ordem).
OpenRouter grátis: 20 req/min e 50 req/dia (1000/dia com ≥ 10 créditos comprados).

### Geradores de vídeo (precisa de pelo menos um)
| Variável | Observação |
|---|---|
| `MAGIC_HOUR_API_KEY` | créditos grátis no cadastro — https://magichour.ai/developer |
| `FAL_KEY` | 1 chave, vários modelos; pago por uso — https://fal.ai/dashboard/keys |
| `REPLICATE_API_TOKEN` | pago por uso — https://replicate.com/account/api-tokens |
| `VIDEO_PROVIDERS` / `PROXY_URL` | seu proxy estilo OpenAI (`/videos`) |
| `MPT_URL` | MoneyPrinterTurbo próprio (precisa de servidor; adaptador ainda sem teste real) |

Extras: `VIDEO_ORDER=mpt,proxy,magichour,fal,replicate`, `FAL_VIDEO_MODEL`,
`REPLICATE_VIDEO_MODEL`, `MAGIC_HOUR_MODEL`, `MAGIC_HOUR_SECONDS`, `*_EXTRA_INPUT` (JSON).

### Proteção
- `RATE_LIMIT_PER_HOUR` (padrão 20 pedidos por IP) — evita gastarem seus créditos grátis.
- Mesma ideia + plataforma reaproveita o roteiro (6 h) e o vídeo (1 h): não gasta crédito de novo.
  Envie `"regenerar": true` para ignorar o cache.
- `PUBLIC_URL` — URL pública do backend (usada quando o download do vídeo precisa de token).

## Endpoints
- `POST /api/gerar` `{"ideia": "...", "plataforma": "instagram reels", "regenerar": false}` → `{"job_id"}`
- `GET /api/status/{job_id}` → `status` (`processando`|`pronto`|`erro`), `passo` (0–3), `texto`,
  `video_url`, `api_llm`, `api_video`, `tokens`
- `POST /api/comprimir` `{"texto": "...", "modo": "auto|light|v24"}` → testa o Ttokem
- `GET /health` → provedores configurados e em cooldown (sem expor chaves)
- `GET /` → ping (acorda o container)

## Rodar / testar
```
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
python -m unittest discover -s tests -v
```

## Sobre o Ttokem
`ttokem.py` é a versão **local** do prompt v24: não gasta chamada de IA. Só comprime textos
longos (≥ 600 caracteres) e só se economizar ≥ 10%; ideias curtas passam intactas. O formato
codebook (`§ID`) é para IAs de texto; **prompts de vídeo nunca usam codebook**.
A economia mostrada é **estimada** (≈ 3,6 caracteres/token), não contada por tokenizador.
