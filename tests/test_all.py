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
    fa.FastAPI, fa.HTTPException, fa.Request = (lambda **k: _App()), HTTPException, object
    cors.CORSMiddleware = object
    sys.modules.update({"fastapi": fa, "fastapi.middleware": mw, "fastapi.middleware.cors": cors})

import ttokem, maestro, video, main, gitops, audio, trending

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


_DESCOBRIR = maestro.Maestro._descobrir      # original (os testes não saem para a internet)


class Base(unittest.TestCase):
    def setUp(self):
        p0 = mock.patch.object(maestro.Maestro, "_descobrir", lambda *a, **k: None)
        p0.start(); self.addCleanup(p0.stop)
        clean = {k: v for k, v in os.environ.items()
                 if not (k.endswith(("_API_KEY", "_API_KEYS", "_KEY", "_TOKEN")) or
                         k in ("VIDEO_PROVIDERS", "PROXY_URL", "LLM_ORDER", "FAL_KEY", "MPT_URL",
                                   "REPLICATE_API_TOKEN", "VIDEO_ORDER", "MAGIC_HOUR_API_KEY"))}
        for k in ("HF_TOKEN", "MAGIC_HOUR_KEY", "GOOGLE_API_KEY"):
            clean.pop(k, None)
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
            url, who, dl = video.VideoMaestro(poll_every=0).generate("a cat")
        self.assertEqual(url, "http://b/v1/videos/j1/content"); self.assertEqual(who, "http://b/v1")
        self.assertIsNone(dl)

    def test_fal_fluxo_queue(self):
        os.environ["FAL_KEY"] = "fk"
        seen = {}
        def post(url, **kw):
            seen["url"], seen["hdr"], seen["body"] = url, kw["headers"], kw["json"]
            return Resp(200, {"request_id": "r1", "status_url": "https://queue.fal.run/x/requests/r1/status",
                              "response_url": "https://queue.fal.run/x/requests/r1"})
        def get(url, **kw):
            if url.endswith("/status"): return Resp(200, {"status": "COMPLETED"})
            return Resp(200, {"video": {"url": "https://v3.fal.media/a.mp4"}})
        with mock.patch("requests.post", post), mock.patch("requests.get", get):
            url, who, dl = video.VideoMaestro(poll_every=0).generate("a cat")
        self.assertEqual(url, "https://v3.fal.media/a.mp4"); self.assertTrue(who.startswith("fal:"))
        self.assertEqual(seen["hdr"]["Authorization"], "Key fk")
        self.assertTrue(seen["url"].startswith("https://queue.fal.run/fal-ai/"))
        self.assertEqual(seen["body"]["aspect_ratio"], "9:16")

    def test_replicate_fluxo_e_download_com_token(self):
        os.environ["REPLICATE_API_TOKEN"] = "rt"
        post = lambda url, **kw: Resp(201, {"id": "p1", "status": "starting", "urls": {"get": "https://api.replicate.com/v1/predictions/p1"}})
        get = lambda url, **kw: Resp(200, {"status": "succeeded", "urls": {"get": url},
                                           "output": ["https://api.replicate.com/v1/files/abc/video.mp4"]})
        with mock.patch("requests.post", post), mock.patch("requests.get", get):
            url, who, dl = video.VideoMaestro(poll_every=0).generate("a cat")
        self.assertIn("/v1/files/", url); self.assertEqual(dl["Authorization"], "Bearer rt")

    def test_fal_sem_credito_cai_para_replicate(self):
        os.environ.update(FAL_KEY="fk", REPLICATE_API_TOKEN="rt")
        def post(url, **kw):
            if "queue.fal.run" in url: return Resp(403)
            return Resp(201, {"id": "p", "status": "succeeded", "output": "https://replicate.delivery/v.mp4",
                              "urls": {"get": "x"}})
        with mock.patch("requests.post", post):
            url, who, dl = video.VideoMaestro(poll_every=0).generate("a cat")
        self.assertEqual(url, "https://replicate.delivery/v.mp4"); self.assertTrue(who.startswith("replicate:"))


class TestSuaConfiguracao(Base):
    """As variáveis que aparecem no SnapDeploy do usuário."""
    def test_variaveis_do_snapdeploy_sao_reconhecidas(self):
        os.environ.update(GEMINI_KEY="g", GROQ_API_KEY="q", HF_TOKEN="hf_x",
                          OPENROUTER_API_KEY="o", MAGIC_HOUR_API_KEY="mhk_live_z")
        nomes = {s["name"] for s in maestro.Maestro().slots()}
        self.assertEqual(nomes, {"gemini", "groq", "huggingface", "openrouter"})
        self.assertEqual([p["kind"] for p in video._providers()], ["magichour"])

    def test_variacao_de_nome_magic_hour(self):
        os.environ["MAGIC_HOUR_KEY"] = "k"
        self.assertEqual([p["kind"] for p in video._providers()], ["magichour"])

    def test_health_mostra_ativos_sem_vazar_chaves(self):
        os.environ.update(GROQ_API_KEY="SEGREDO123", MAGIC_HOUR_API_KEY="SEGREDO456")
        with mock.patch.object(main, "llm", maestro.Maestro()), \
             mock.patch.object(main, "videos", video.VideoMaestro()):
            h = main.health()
        self.assertEqual(h["ativos"], {"texto": ["groq"], "video": ["magichour"]})
        self.assertNotIn("SEGREDO", str(h))


class TestNovos(Base):
    def test_magichour_fluxo_e_retry_sem_aspect(self):
        os.environ["MAGIC_HOUR_API_KEY"] = "mhk_live_x"
        bodies = []
        def post(url, **kw):
            bodies.append(dict(kw["json"]))
            return Resp(422) if "aspect_ratio" in kw["json"] else Resp(200, {"id": "v1", "credits_charged": 5})
        get = lambda url, **kw: Resp(200, {"status": "complete", "downloads": [{"url": "https://cdn.mh/v.mp4"}]})
        with mock.patch("requests.post", post), mock.patch("requests.get", get):
            url, who, dl = video.VideoMaestro(poll_every=0).generate("a dog running")
        self.assertEqual(url, "https://cdn.mh/v.mp4"); self.assertEqual(who, "magichour:default")
        self.assertEqual(bodies[0]["style"], {"prompt": "a dog running"}); self.assertNotIn("aspect_ratio", bodies[1])

    def test_magichour_sem_credito_vai_para_proximo(self):
        os.environ.update(MAGIC_HOUR_API_KEY="k", FAL_KEY="f")
        def post(url, **kw):
            if "magichour" in url: return Resp(402)
            return Resp(200, {"status_url": "https://queue.fal.run/s", "response_url": "https://queue.fal.run/r"})
        def get(url, **kw):
            return Resp(200, {"status": "COMPLETED"}) if url.endswith("/s") else Resp(200, {"video": {"url": "https://f/v.mp4"}})
        with mock.patch("requests.post", post), mock.patch("requests.get", get):
            url, who, dl = video.VideoMaestro(poll_every=0).generate("x")
        self.assertEqual(url, "https://f/v.mp4")

    def test_mpt_fluxo(self):
        os.environ["MPT_URL"] = "http://mpt:8080"
        post = lambda url, **kw: Resp(200, {"status": 200, "data": {"task_id": "t1"}})
        get = lambda url, **kw: Resp(200, {"data": {"state": 1, "progress": 100, "videos": ["http://mpt:8080/tasks/t1/final-1.mp4"]}})
        with mock.patch("requests.post", post), mock.patch("requests.get", get):
            url, who, dl = video.VideoMaestro(poll_every=0).generate("tema")
        self.assertTrue(url.endswith("final-1.mp4"))

    def test_cache_evita_segunda_chamada_de_ia(self):
        os.environ.update(GROQ_API_KEY="a", VIDEO_PROVIDERS="http://a/v1")
        main.LLM_CACHE.clear(); main.VIDEO_CACHE.clear()
        calls = {"llm": 0, "vid": 0}
        def post(url, **kw):
            if "/chat/completions" in url:
                calls["llm"] += 1; return ok('{"legenda":"L","prompt_video":"cat"}')
            calls["vid"] += 1; return Resp(200, {"id": "j"})
        with mock.patch("requests.post", post), \
             mock.patch("requests.get", lambda *a, **k: Resp(200, {"status": "completed"})), \
             mock.patch.object(main, "llm", maestro.Maestro()), \
             mock.patch.object(main, "videos", video.VideoMaestro(poll_every=0)):
            run_job("gato cache"); j = run_job("gato cache")
        self.assertEqual(calls, {"llm": 1, "vid": 1}); self.assertIn("(cache)", j["api_llm"])

    def test_rate_limit_por_ip(self):
        class Rq:
            headers = {"x-forwarded-for": "9.9.9.9"}
            client = None
        main.RATE.clear()
        with mock.patch.object(main, "RATE_LIMIT", 2):
            r = [main._limite(Rq()) for _ in range(3)]
        self.assertEqual(r, [True, True, False])


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


class TestColab(Base):
    def test_registro_exige_segredo(self):
        os.environ.pop("COLAB_SECRET", None)
        with self.assertRaises(main.HTTPException) as c:
            main.colab_registrar(main.ColabReq(url="https://a.trycloudflare.com", secret="x"))
        self.assertEqual(c.exception.status_code, 403)
        os.environ["COLAB_SECRET"] = "s3"
        with self.assertRaises(main.HTTPException):
            main.colab_registrar(main.ColabReq(url="https://a.trycloudflare.com", secret="errado"))
        main.colab_registrar(main.ColabReq(url="https://a.trycloudflare.com/", secret="s3"))
        self.assertEqual(video._providers()[0]["id"], "colab")
        video.COLAB["ts"] = 0          # sem sinal de vida: sai da fila
        self.assertFalse([p for p in video._providers() if p["kind"] == "colab"])

    def test_adaptador_colab_devolve_download_com_token(self):
        os.environ["COLAB_URL"] = "http://colab.test"; os.environ["COLAB_SECRET"] = "s3"
        post = lambda url, **kw: Resp(200, {"job_id": "abc123"})
        get = lambda url, **kw: Resp(200, {"status": "done"})
        with mock.patch("requests.post", post), mock.patch("requests.get", get):
            url, who, dl = video.VideoMaestro(poll_every=0).generate("cachorro")
        self.assertEqual((url, who, dl), ("http://colab.test/video/abc123", "colab", {"X-Token": "s3"}))

    def test_colab_offline_cai_para_o_proximo(self):
        os.environ["COLAB_URL"] = "http://colab.test"; os.environ["FAL_KEY"] = "k"
        def post(url, **kw):
            if "colab.test" in url: raise maestro.requests.ConnectionError("off")
            return Resp(200, {"status_url": "https://queue.fal.run/s", "response_url": "https://queue.fal.run/r"})
        get = lambda url, **kw: Resp(200, {"status": "COMPLETED"}) if url.endswith("/s") else Resp(200, {"video": {"url": "https://f/v.mp4"}})
        with mock.patch("requests.post", post), mock.patch("requests.get", get):
            url, who, dl = video.VideoMaestro(poll_every=0).generate("x")
        self.assertTrue(who.startswith("fal"))

    def test_nome_de_variavel_cortado_no_painel(self):
        os.environ["MAGIC_HOUR_"] = "chave"
        self.assertTrue([p for p in video._providers() if p["kind"] == "magichour"])

    def test_chat_responde_e_limita(self):
        os.environ["GROQ_API_KEY"] = "a"
        with mock.patch.object(maestro.requests, "post", lambda *a, **k: ok("oi!")), \
             mock.patch.object(main, "llm", maestro.Maestro()):
            class Rq: headers = {}; client = types.SimpleNamespace(host="9.9.9.9")
            r = main.chat(main.ChatReq(mensagem="ola"), Rq())
        self.assertEqual(r["resposta"], "oi!")


class TestGitEAudio(Base):
    def _rq(self, secret="seg", q=None):
        return types.SimpleNamespace(headers={"x-build-secret": secret} if secret else {}, query_params=q or {},
                                     client=types.SimpleNamespace(host="1.1.1.1"))

    def test_build_desligado_sem_config(self):
        with self.assertRaises(main.HTTPException) as c:
            main.build_iniciar(self._rq())
        self.assertEqual(c.exception.status_code, 503)

    def test_build_senha_errada(self):
        os.environ.update(GITHUB_TOKEN="t", BUILD_SECRET="seg")
        with self.assertRaises(main.HTTPException) as c:
            main.build_iniciar(self._rq("errada"))
        self.assertEqual(c.exception.status_code, 403)
        main._auth_build(self._rq("seg")); main._auth_build(self._rq("", {"s": "seg"}))

    def test_caminhos_perigosos_bloqueados(self):
        for bad in (".github/workflows/build.yml", "../x", "/etc/passwd", ".git/config", ""):
            self.assertFalse(gitops.seguro(bad), bad)
        self.assertTrue(gitops.seguro("main.py")); self.assertTrue(gitops.seguro("pasta/a.py"))

    def test_enviar_arquivo_cria_branch_e_commita(self):
        os.environ.update(GITHUB_TOKEN="t", BUILD_SECRET="seg")
        calls = []
        def request(method, url, **kw):
            calls.append((method, url.split("/repos/")[1])); 
            if "git/ref/heads/main" in url: return Resp(200, {"object": {"sha": "abc"}})
            if "git/refs" in url: return Resp(201, {})
            if method == "PUT": return Resp(200, {"commit": {"sha": "1234567890"}})
            return Resp(200, {})
        def get(url, **kw):
            calls.append(("GET", url.split("/repos/")[1]))
            return Resp(404, {}) if ("heads/ia-build" in url or "contents/" in url) else Resp(200, {})
        with mock.patch("requests.request", request), mock.patch("requests.get", get):
            r = main.build_arquivo(main.ArquivoReq(path="main.py", conteudo="print(1)"), self._rq())
        self.assertEqual((r["commit"], r["branch"]), ("1234567", "ia-build"))
        self.assertTrue(any(m == "POST" and u.endswith("git/refs") for m, u in calls))
        with self.assertRaises(main.HTTPException):
            main.build_arquivo(main.ArquivoReq(path=".github/x.yml", conteudo="a"), self._rq())

    def test_status_e_apk(self):
        import io, zipfile
        os.environ.update(GITHUB_TOKEN="t", BUILD_SECRET="seg")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z: z.writestr("bin/app.apk", b"APKDATA")
        def request(method, url, **kw):
            if url.endswith("/actions/runs/7"): return Resp(200, {"status": "completed", "conclusion": "success", "html_url": "https://gh/7"})
            if url.endswith("/runs/7/artifacts"): return Resp(200, {"artifacts": [{"name": "ttoken-apk", "id": 9}]})
            if url.endswith("/artifacts/9/zip"):
                r = Resp(200); r.content = buf.getvalue(); return r
        with mock.patch("requests.request", request):
            self.assertEqual(main.build_status(7, self._rq())["conclusao"], "success")
            nome, dados = gitops.baixar_apk(7)
        self.assertEqual((nome, dados), ("app.apk", b"APKDATA"))

    def test_transcricao(self):
        with self.assertRaises(audio.SemTranscricao): audio.transcrever(b"x")
        os.environ["GROQ_API_KEY"] = "g"
        with mock.patch("requests.post", lambda *a, **k: Resp(200, {"text": " ola mundo "})):
            self.assertEqual(audio.transcrever(b"x"), "ola mundo")


class TestMaestroErros(Base):
    def test_erro_real_aparece_e_cooldown_nao_trava(self):
        os.environ["GROQ_API_KEY"] = "ruim"
        calls = []
        def post(url, **kw):
            calls.append(url)
            return Resp(401, {"error": {"message": "Invalid API Key"}})
        m = maestro.Maestro()
        with mock.patch.object(maestro.requests, "post", post):
            with self.assertRaises(maestro.AllProvidersFailed) as c:
                m.chat([{"role": "user", "content": "oi"}])
            self.assertIn("Invalid API Key", str(c.exception))
            n1 = len(calls)
            with self.assertRaises(maestro.AllProvidersFailed):
                m.chat([{"role": "user", "content": "oi"}])      # tudo esfriando: tenta de novo
            self.assertGreater(len(calls), n1)
        self.assertIn("Invalid", m.status()[0]["ultimo_erro"])

    def test_testar_e_recuperacao(self):
        os.environ["GROQ_API_KEY"] = "k"
        m = maestro.Maestro()
        with mock.patch.object(maestro.requests, "post", lambda *a, **k: Resp(429, {})):
            self.assertFalse(m.testar()[0]["ok"])
        with mock.patch.object(maestro.requests, "post", lambda *a, **k: ok("ok")):
            self.assertTrue(m.testar()[0]["ok"])
            self.assertEqual(m.chat([{"role": "user", "content": "x"}])[0], "ok")


class TestEstimativa(Base):
    def test_estimativa_do_primeiro_gerador(self):
        os.environ["MAGIC_HOUR_API_KEY"] = "k"
        self.assertEqual(video.VideoMaestro().proxima(), ("magichour", 1, 3))
        del os.environ["MAGIC_HOUR_API_KEY"]
        self.assertIsNone(video.VideoMaestro().proxima())      # sem chave: nenhum


class TestDescoberta(Base):
    def test_modelos_descobertos_substituem_os_desligados(self):
        os.environ["GROQ_API_KEY"] = "g"; os.environ["OPENROUTER_API_KEY"] = "o"
        def get(url, **kw):
            if "groq" in url:
                return Resp(200, {"data": [{"id": "whisper-large-v3"}, {"id": "llama-3.3-70b-versatile-old"},
                                           {"id": "openai/gpt-oss-120b"}, {"id": "openai/gpt-oss-safeguard-20b"}]})
            return Resp(200, {"data": [
                {"id": "a/pago", "pricing": {"prompt": "0.1", "completion": "0.1"}, "context_length": 9e5},
                {"id": "b/gratis:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 100000},
                {"id": "c/gratis:free", "pricing": {"prompt": "0", "completion": "0"}, "context_length": 200000}]})
        m = maestro.Maestro()
        with mock.patch.object(maestro.requests, "get", get):
            _DESCOBRIR(m, "groq", maestro.PROVIDERS["groq"][0], "g")
            _DESCOBRIR(m, "openrouter", maestro.PROVIDERS["openrouter"][0], "o")
        modelos = [s["model"] for s in m.slots()]
        self.assertEqual(modelos[0], "openai/gpt-oss-120b")
        self.assertNotIn("whisper-large-v3", modelos); self.assertNotIn("openai/gpt-oss-safeguard-20b", modelos)
        self.assertIn("openrouter/free", modelos); self.assertIn("c/gratis:free", modelos); self.assertNotIn("a/pago", modelos)

    def test_gemini_ignora_pro_e_usa_flash_lite_primeiro(self):
        os.environ["GEMINI_KEY"] = "k"
        get = lambda url, **kw: Resp(200, {"data": [{"id": "models/gemini-2.5-pro"}, {"id": "models/gemini-2.5-flash"},
                                                    {"id": "models/gemini-3.1-flash-lite"}, {"id": "models/text-embedding-004"}]})
        m = maestro.Maestro()
        with mock.patch.object(maestro.requests, "get", get):
            _DESCOBRIR(m, "gemini", maestro.PROVIDERS["gemini"][0], "k")
        self.assertEqual([s["model"] for s in m.slots()], ["gemini-3.1-flash-lite", "gemini-2.5-flash"])

    def test_modelo_fixo_por_env_nao_e_trocado(self):
        os.environ["GROQ_API_KEY"] = "g"; os.environ["GROQ_MODEL"] = "meu/modelo"
        m = maestro.Maestro()
        with mock.patch.object(maestro.requests, "get", lambda *a, **k: self.fail("nao deveria consultar")):
            _DESCOBRIR(m, "groq", maestro.PROVIDERS["groq"][0], "g")
        self.assertEqual(m.slots()[0]["model"], "meu/modelo")


class TestTendenciasEPexels(Base):
    def setUp(self):
        super().setUp(); trending._CACHE.clear()

    def test_mais_vistos_ordenado_e_cache(self):
        os.environ["YOUTUBE_API_KEY"] = "yk"
        n = []
        def get(url, **kw):
            n.append(url)
            return Resp(200, {"items": [
                {"id": "a1", "snippet": {"title": "A", "channelTitle": "C1", "thumbnails": {"medium": {"url": "u"}}}, "statistics": {"viewCount": "1500"}},
                {"id": "b2", "snippet": {"title": "B", "channelTitle": "C2"}, "statistics": {"viewCount": "2500000", "likeCount": "9"}}]})
        with mock.patch("requests.get", get):
            r = trending.mais_vistos("br")
            trending.mais_vistos("br")
        self.assertEqual([i["id"] for i in r], ["b2", "a1"]); self.assertEqual(r[0]["views"], 2500000)
        self.assertEqual(len(n), 1)                       # segunda chamada veio do cache (poupa a cota)

    def test_sem_chave_e_erro_da_api(self):
        with self.assertRaises(main.HTTPException) as c:
            main.tendencias(types.SimpleNamespace(headers={}, client=types.SimpleNamespace(host="3.3.3.3")))
        self.assertEqual(c.exception.status_code, 503)
        os.environ["YOUTUBE_API_KEY"] = "ruim"
        with mock.patch("requests.get", lambda *a, **k: Resp(403, {"error": {"message": "API key not valid"}})):
            with self.assertRaises(main.HTTPException) as c:
                main.tendencias(types.SimpleNamespace(headers={}, client=types.SimpleNamespace(host="3.3.3.4")))
        self.assertEqual(c.exception.status_code, 502); self.assertIn("not valid", c.exception.detail)

    def test_pexels_como_ultimo_recurso_e_aviso(self):
        os.environ["PEXELS_API_KEY"] = "pk"
        get = lambda url, **kw: Resp(200, {"videos": [{"video_files": [
            {"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "https://p/v.mp4"}]}]})
        with mock.patch("requests.get", get):
            url, who, dl = video.VideoMaestro(poll_every=0).generate("a dog running in a field")
        self.assertEqual((url, who, dl), ("https://p/v.mp4", "pexels", None))


class TestAnuncioFotoMeuVideo(Base):
    JPG = b"\xff\xd8\xff\xe0" + b"0" * 50

    def _rq(self):
        return types.SimpleNamespace(headers={}, client=types.SimpleNamespace(host="7.7.7.7"))

    def test_foto_valida_vira_url_temporaria_e_serve_bytes(self):
        import base64
        url = main._guardar_imagem(base64.b64encode(self.JPG).decode())
        self.assertIn("/api/img/", url)
        guardada = main.IMGS[url.rsplit("/", 1)[1]]
        self.assertEqual((guardada[0], guardada[1]), (self.JPG, "image/jpeg"))
        with self.assertRaises(main.HTTPException):          # id desconhecido/expirado
            main.baixar_imagem("naoexiste")
        with self.assertRaises(main.HTTPException): main._guardar_imagem(base64.b64encode(b"nao sou imagem").decode())
        with self.assertRaises(main.HTTPException): main._guardar_imagem(base64.b64encode(self.JPG + b"0" * 4_300_000).decode())

    def test_foto_para_video_usa_magichour_image_to_video(self):
        os.environ["MAGIC_HOUR_API_KEY"] = "k"; os.environ["FAL_KEY"] = ""
        sent = {}
        def post(url, **kw):
            sent["url"], sent["body"] = url, kw["json"]; return Resp(200, {"id": "p1"})
        get = lambda url, **kw: Resp(200, {"status": "complete", "downloads": [{"url": "https://m/v.mp4"}]})
        with mock.patch("requests.post", post), mock.patch("requests.get", get):
            url, who, dl = video.VideoMaestro(poll_every=0).generate("animate product", "https://x/api/img/abc")
        self.assertTrue(sent["url"].endswith("/v1/image-to-video"))
        self.assertEqual(sent["body"]["assets"], {"image_file_path": "https://x/api/img/abc"})
        self.assertNotIn("aspect_ratio", sent["body"]); self.assertEqual(who, "magichour:default+foto")

    def test_foto_ignora_geradores_sem_foto_para_video(self):
        os.environ["REPLICATE_API_TOKEN"] = "r"
        with self.assertRaises(video.VideoFailed):
            video.VideoMaestro(poll_every=0).generate("x", "https://x/api/img/abc")

    def test_pipeline_anuncio_usa_prompt_de_comercial_e_cai_para_texto(self):
        os.environ["GROQ_API_KEY"] = "a"
        visto = {}
        class V:
            def proxima(s): return None
            def generate(s, prompt, imagem=""):
                if imagem: raise video.VideoFailed("sem gerador de foto")
                return "https://v/t.mp4", "colab", None
        def post(url, **kw):
            visto["system"] = kw["json"]["messages"][0]["content"]
            return ok('{"titulo":"T","legenda":"Compre!","hashtags":["#a"],"prompt_video":"cinematic perfume"}')
        out = {}
        with mock.patch.object(maestro.requests, "post", post):
            main.pipeline.produzir("perfume dourado", "instagram", maestro.Maestro(), V(),
                                   lambda **kw: out.update(kw), tipo="anuncio", imagem_url="https://x/i")
        self.assertIn("diretor de arte", visto["system"])
        self.assertEqual(out["status"], "pronto"); self.assertIn("Foto nao usada", out["aviso"])

    def test_sem_video_so_legenda(self):
        os.environ["GROQ_API_KEY"] = "a"
        class V:
            def proxima(s): return None
            def generate(s, *a): raise AssertionError('nao deveria gerar video')
        out = {}
        with mock.patch.object(maestro.requests, "post", lambda *a, **k: ok('{"legenda":"L","prompt_video":"p"}')):
            main.pipeline.produzir("meu video", "tiktok", maestro.Maestro(), V(),
                                   lambda **kw: out.update(kw), sem_video=True)
        self.assertEqual((out["status"], out["api_video"], out["texto"]), ("pronto", "seu video", "L"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
