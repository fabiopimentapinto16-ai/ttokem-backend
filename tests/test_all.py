import os, sys, time, types, unittest
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Se o fastapi não estiver instalado (ambiente sem internet), usa um stub mínimo
# só para importar main.py e testar a lógica dos endpoints chamando as funções.
try:
    import fastapi  # noqa
except ModuleNotFoundError:
    fa = types.ModuleType("fastapi"); mw = types.ModuleType("fastapi.middleware"); cors = types.ModuleType("fastapi.middleware.cors")
    class _App:
        def add_middleware(self, *a, **k): pass
        def _d(self, *a, **k): return lambda f: f
        get = post = _d
    class HTTPException(Exception):
        def __init__(self, status_code, detail=""): self.status_code, self.detail = status_code, detail
    fa.FastAPI, fa.HTTPException = (lambda **k: _App()), HTTPException
    cors.CORSMiddleware = object
    sys.modules.update({"fastapi": fa, "fastapi.middleware": mw, "fastapi.middleware.cors": cors})

import ttokem, maestro, video, main

TXT = ("Relato do incidente INC-2026-4821 em 2026-10-15 às 14:32 UTC. Basicamente, "
       "o serviço PaymentService.charge falhou em payment.js:142 com TIMEOUT_ERROR e "
       "status 504 no gateway=stripe-code=504. A transação txn_9f8a7b6c5d ficou pending "
       "e o ticket #48291 foi aberto. Precisamos fazer rollback do deploy. Deve-se "
       "reprocessar a transação txn_9f8a7b6c5d. Próximo passo: notificar finance@x.com "
       "e verificar https://status.example.com/i/4821. "
       + "Foi uma situação muito complicada, mas estamos trabalhando bem. " * 6)


class Resp:
    def __init__(s, code=200, js=None, headers=None, text=""):
        s.status_code, s._js, s.headers, s.text = code, js, headers or {}, text
    def json(s): return s._js
    def raise_for_status(s):
        if s.status_code >= 400:
            raise maestro.requests.HTTPError(str(s.status_code))


def ok(text): return Resp(200, {"choices": [{"message": {"content": text}}]})


class Base(unittest.TestCase):
    def setUp(self):
        clean = {k: v for k, v in os.environ.items()
                 if not (k.endswith(("_API_KEY", "_API_KEYS", "_KEY")) or
                         k in ("VIDEO_PROVIDERS", "PROXY_URL", "LLM_ORDER"))}
        p = mock.patch.dict(os.environ, clean, clear=True)
        p.start(); self.addCleanup(p.stop)


class TestTtokem(Base):
    def test_v24_preserva_criticos_e_economiza(self):
        r = ttokem.compress(TXT, "v24")
        self.assertFalse(r["refusal"]); self.assertEqual(r["lost"], [])
        for c in ["INC-2026-4821", "payment.js:142", "TIMEOUT_ERROR", "txn_9f8a7b6c5d",
                  "#48291", "14:32 UTC", "https://status.example.com/i/4821", "finance@x.com"]:
            self.assertIn(c, r["text"])
        self.assertGreater(r["saving_pct"], 0)
        self.assertTrue(r["formatted"].startswith("⚡ [TTOKEM v24"))

    def test_ideia_curta_passa_direto(self):
        r = ttokem.compress("Um gato astronauta dançando na lua", "auto")
        self.assertEqual(r["mode"], "passthrough")
        self.assertTrue(r["text"].startswith("Um gato"))

    def test_light_nao_usa_codebook(self):
        r = ttokem.compress("Por favor, basicamente faça um vídeo de um cachorro. "
                            "Por favor, basicamente faça um vídeo de um cachorro.", "light")
        self.assertNotIn("§", r["text"]); self.assertNotIn("CODEBOOK", r["text"])
        self.assertEqual(r["text"].count("cachorro"), 1)


class TestMaestro(Base):
    def test_troca_quando_credito_acaba(self):
        os.environ.update(LLM_ORDER="groq,gemini", GROQ_API_KEY="g1", GEMINI_KEY="m1")
        calls = []
        def post(url, **kw):
            calls.append(url)
            return Resp(429, headers={"Retry-After": "120"}) if "groq" in url else ok("oi")
        with mock.patch.object(maestro.requests, "post", post):
            m = maestro.Maestro()
            text, who = m.chat([{"role": "user", "content": "x"}])
            self.assertEqual(text, "oi"); self.assertTrue(who.startswith("gemini"))
            m.chat([{"role": "user", "content": "x"}])   # groq em cooldown: não repete
        self.assertEqual(sum("groq" in c for c in calls), 1)

    def test_varias_chaves_do_mesmo_provedor(self):
        os.environ["GROQ_API_KEYS"] = "a,b"
        def post(url, headers, **kw):
            return Resp(429) if headers["Authorization"].endswith("a") else ok("da chave b")
        with mock.patch.object(maestro.requests, "post", post):
            self.assertEqual(maestro.Maestro().chat([{"role": "user", "content": "x"}])[0], "da chave b")

    def test_todos_falham(self):
        os.environ["GROQ_API_KEY"] = "a"
        with mock.patch.object(maestro.requests, "post", lambda *a, **k: Resp(500)):
            with self.assertRaises(maestro.AllProvidersFailed):
                maestro.Maestro().chat([{"role": "user", "content": "x"}])


class TestVideo(Base):
    def test_video_troca_de_gerador(self):
        os.environ["VIDEO_PROVIDERS"] = "http://a/v1,http://b/v1"
        post = lambda url, **kw: Resp(429) if "//a/" in url else Resp(200, {"id": "j1"})
        get = lambda url, **kw: Resp(200, {"status": "completed"})
        with mock.patch.object(video.requests, "post", post), mock.patch.object(video.requests, "get", get):
            url, who = video.VideoMaestro(poll_every=0).generate("a cat")
        self.assertEqual(url, "http://b/v1/videos/j1/content"); self.assertEqual(who, "http://b/v1")


def run_job(ideia):
    with main.JOBS_LOCK:
        main.JOBS["t"] = {"status": "processando", "etapa": "", "criado": time.time(), "atualizado": time.time()}
    main._run("t", main.GerarReq(ideia=ideia))
    return dict(main.JOBS["t"])


class TestPipeline(Base):
    def test_fluxo_completo(self):
        os.environ.update(GROQ_API_KEY="a", VIDEO_PROVIDERS="http://a/v1")
        js = '```json\n{"titulo":"T","roteiro":"r","legenda":"Olha só!","hashtags":["#a","#b"],"prompt_video":"a cat on the moon"}\n```'
        sent = {}
        def post(url, **kw):            # um único mock: requests é o mesmo módulo
            if "/chat/completions" in url:
                return ok(js)
            sent.update(kw["json"]); return Resp(200, {"id": "j"})
        with mock.patch("requests.post", post), \
             mock.patch("requests.get", lambda *a, **k: Resp(200, {"status": "completed"})), \
             mock.patch.object(main, "llm", maestro.Maestro()), \
             mock.patch.object(main, "videos", video.VideoMaestro(poll_every=0)):
            j = run_job("gato astronauta")
        self.assertEqual(j["status"], "pronto")
        self.assertIn("Olha só!", j["texto"]); self.assertIn("#a #b", j["texto"])
        self.assertTrue(j["video_url"].endswith("/videos/j/content"))
        self.assertEqual(sent["prompt"], "a cat on the moon")   # prompt natural, não codebook

    def test_sem_ia_retorna_erro_claro(self):
        with mock.patch.object(main, "llm", maestro.Maestro()):
            j = run_job("gato")
        self.assertEqual(j["status"], "erro"); self.assertIn("Nenhuma IA", j["erro"])

    def test_video_cai_mas_legenda_fica(self):
        os.environ["GROQ_API_KEY"] = "a"
        with mock.patch.object(maestro.requests, "post", lambda *a, **k: ok('{"legenda":"L","prompt_video":"p"}')), \
             mock.patch.object(main, "llm", maestro.Maestro()), \
             mock.patch.object(main, "videos", video.VideoMaestro()):
            j = run_job("gato")
        self.assertEqual(j["status"], "pronto"); self.assertEqual(j["video_url"], "")
        self.assertIn("indisponível", j["aviso"]); self.assertEqual(j["texto"], "L")


if __name__ == "__main__":
    unittest.main(verbosity=2)
