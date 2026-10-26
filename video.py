"""Maestro de vídeo: tenta cada gerador na ordem; se um falhar ou ficar sem
crédito, passa ao próximo.

Geradores suportados (ordem padrão: mpt, proxy, magichour, fal, replicate —
os gratuitos/self-hosted primeiro; mude com VIDEO_ORDER):
  proxy     endpoints "job assíncrono" estilo OpenAI (o seu proxy atual)
            VIDEO_PROVIDERS="base1|modelo|chave, base2" e/ou PROXY_URL (legado)
  fal       fal.ai  — 1 chave, muitos modelos de vídeo
            FAL_KEY, FAL_VIDEO_MODEL, FAL_EXTRA_INPUT (JSON opcional)
  replicate Replicate — 1 token, muitos modelos de vídeo
            REPLICATE_API_TOKEN, REPLICATE_VIDEO_MODEL, REPLICATE_EXTRA_INPUT
  magichour Magic Hour — plano grátis com créditos (modelo "default" = ltx-2.5)
            MAGIC_HOUR_API_KEY, MAGIC_HOUR_MODEL, MAGIC_HOUR_SECONDS,
            MAGIC_HOUR_EXTRA_INPUT
  mpt       MoneyPrinterTurbo self-hosted (precisa de servidor próprio)
            MPT_URL, MPT_BASIC_AUTH ("usuario:senha", opcional)
            (adaptador escrito pela API publicada; ainda sem servidor real testado)
  tavus     Tavus — avatar falando o roteiro (não é vídeo de cena; fica por último)
            TAVUS_API_KEY, TAVUS_REPLICA_ID (opcional)
  bigmodel  Zhipu BigModel (CogVideoX-Flash) — BIGMODEL_API_KEY
  colab     SEU Google Colab (GPU T4 grátis) rodando um modelo aberto de vídeo:
            sem custo por vídeo, sem cota de API. O notebook em colab/ se registra
            sozinho no backend (COLAB_SECRET) — ou fixe COLAB_URL.
generate() devolve (url, gerador, headers_de_download | None).
"""
import json
import os
import re
import threading
import time

import requests


class VideoFailed(Exception):
    pass


class NoCredit(VideoFailed):
    """Crédito/limite do gerador acabou: esfria por mais tempo."""


FAL_DEFAULT_MODEL = "fal-ai/bytedance/seedance/v1.5/pro/text-to-video"
REPLICATE_DEFAULT_MODEL = "minimax/video-01"


def _env(*names):
    """Primeira variável de ambiente preenchida (aceita variações de nome)."""
    for n in names:
        v = os.environ.get(n, "").strip()
        if v:
            return v
    return ""


def _env_fuzzy(*parts):
    """Aceita nomes cortados/errados no painel (ex.: MAGIC_HOUR_ sem o resto):
    pega a primeira variável cujo nome contém TODOS os trechos e tem valor."""
    for k, v in sorted(os.environ.items()):
        ku = k.upper()
        if v.strip() and all(x in ku for x in parts):
            return v.strip()
    return ""


# Google Colab se registra aqui (o link do túnel muda a cada sessão)
COLAB = {"url": "", "ts": 0.0}
COLAB_TTL = 15 * 60            # sem sinal de vida por 15 min = offline


def colab_registrar(url: str):
    COLAB["url"], COLAB["ts"] = url.rstrip("/"), time.time()


def _colab_url():
    fixa = os.environ.get("COLAB_URL", "").strip().rstrip("/")
    if COLAB["url"] and time.time() - COLAB["ts"] < COLAB_TTL:
        return COLAB["url"]
    return fixa


def _extra(var, default=None):
    try:
        return json.loads(os.environ.get(var, "")) if os.environ.get(var) else (default or {})
    except ValueError:
        return default or {}


def _providers():
    out = {"pexels": [], "colab": [], "mpt": [], "proxy": [], "magichour": [], "fal": [],
           "replicate": [], "bigmodel": [], "agnes": [], "tavus": []}
    px = _env("PEXELS_API_KEY", "PEXELS_KEY") or _env_fuzzy("PEXELS")
    if px:
        out["pexels"].append({"id": "pexels", "kind": "pexels", "model": "stock", "key": px})
    cu = _colab_url()
    if cu:
        out["colab"].append({"id": "colab", "kind": "colab", "base": cu, "model": "colab",
                             "key": os.environ.get("COLAB_SECRET", "")})
    tv = _env("TAVUS_API_KEY", "TAVUS_API_KI", "TAVUS_KEY") or _env_fuzzy("TAVUS")
    if tv:
        rid = os.environ.get("TAVUS_REPLICA_ID", "rc9cff32ceba").strip() or "rc9cff32ceba"
        out["tavus"].append({"id": f"tavus:{rid[:8]}", "kind": "tavus", "model": rid,
                             "key": tv, "replica_id": rid})
    bm = _env("BIGMODEL_API_KEY", "ZHIPU_API_KEY", "BIGMODEL_KEY") or _env_fuzzy("BIGMODEL")
    if bm:
        m = os.environ.get("BIGMODEL_VIDEO_MODEL", "cogvideox-flash")
        out["bigmodel"].append({"id": f"bigmodel:{m}", "kind": "bigmodel", "model": m, "key": bm})
    ag = _env("AGNES_API_KEY", "AGNES_TOKEN", "AGNES_KEY")
    if ag:
        m = os.environ.get("AGNES_VIDEO_MODEL", "agnes-video-v2.0")
        base = os.environ.get("AGNES_BASE_URL", "https://apihub.agnes-ai.com/v1").rstrip("/")
        out["agnes"].append({"id": f"agnes:{m}", "kind": "agnes", "model": m, "key": ag,
                             "base": base})
    default_model = os.environ.get("VIDEO_MODEL", "wan-fast")
    entries = [e.strip() for e in os.environ.get("VIDEO_PROVIDERS", "").split(",")
               if e.strip()]
    legacy = os.environ.get("PROXY_URL", "").strip()
    if legacy and not any(e.split("|")[0] == legacy for e in entries):
        entries.append(legacy)
    for e in entries:
        parts = [p.strip() for p in e.split("|")]
        base = parts[0].rstrip("/")
        out["proxy"].append({
            "id": base, "kind": "proxy", "base": base,
            "model": parts[1] if len(parts) > 1 and parts[1] else default_model,
            "key": parts[2] if len(parts) > 2 else ""})
    if os.environ.get("MPT_URL"):
        base = os.environ["MPT_URL"].rstrip("/")
        out["mpt"].append({"id": f"mpt:{base}", "kind": "mpt", "base": base,
                           "model": "moneyprinterturbo",
                           "key": os.environ.get("MPT_BASIC_AUTH", "")})
    mh = (_env("MAGIC_HOUR_API_KEY", "MAGIC_HOUR_KEY", "MAGICHOUR_API_KEY",
               "MAGIC_HOUR_TOKEN", "MAGICHOUR_KEY") or _env_fuzzy("MAGIC", "HOUR")
          or _env_fuzzy("MAGICHOUR"))
    if mh:
        m = os.environ.get("MAGIC_HOUR_MODEL", "default")
        out["magichour"].append({"id": f"magichour:{m}", "kind": "magichour",
                                 "model": m, "key": mh})
    fk = _env("FAL_KEY", "FAL_API_KEY")
    if fk:
        m = os.environ.get("FAL_VIDEO_MODEL", FAL_DEFAULT_MODEL)
        out["fal"].append({"id": f"fal:{m}", "kind": "fal", "model": m, "key": fk})
    rk = _env("REPLICATE_API_TOKEN", "REPLICATE_API_KEY", "REPLICATE_TOKEN")
    if rk:
        m = os.environ.get("REPLICATE_VIDEO_MODEL", REPLICATE_DEFAULT_MODEL)
        out["replicate"].append({"id": f"replicate:{m}", "kind": "replicate",
                                 "model": m, "key": rk})
    order = [n.strip() for n in os.environ.get("VIDEO_ORDER", "colab,mpt,proxy,magichour,fal,replicate,bigmodel,agnes,pexels,tavus").split(",")]
    order += [n for n in out if n not in order]
    if os.environ.get("TAVUS_AUTO", "1").strip() == "0":     # avatar só se estiver em VIDEO_ORDER
        explicit = "tavus" in [n.strip() for n in os.environ.get("VIDEO_ORDER", "").split(",")]
        if not explicit:
            order = [n for n in order if n != "tavus"]
    return [p for n in order for p in out.get(n, [])]


# Estimativa (minutos) por gerador — valores aproximados, não garantidos
ETA = {"pexels": (0, 1), "colab": (4, 8), "magichour": (1, 3), "fal": (1, 3), "replicate": (2, 6),
       "bigmodel": (1, 4), "agnes": (1, 5), "tavus": (2, 5), "mpt": (2, 7), "proxy": (1, 5)}


I2V = ("magichour", "fal")      # geradores com foto-para-vídeo (adaptados da documentação)


class VideoMaestro:
    def __init__(self, poll_every=5, max_wait=240):
        self.poll_every, self.max_wait = poll_every, max_wait
        self._cool, self._lock = {}, threading.Lock()
        self.ultimo_erros = []
        self.historico = []             # últimos eventos (para o painel de geradores)
        self._ultimo = {}               # gerador -> {"ok": bool, "quando": ts, "erro": str}

    def _evento(self, pid, ok, erro=""):
        ev = {"gerador": pid, "ok": ok, "erro": erro[:200], "quando": time.time()}
        with self._lock:
            self._ultimo[pid] = ev
            self.historico.append(ev)
            del self.historico[:-40]

    def status(self):
        now = time.time()
        return [{"gerador": p["id"], "tipo": p["kind"],
                 "disponivel": self._cool.get(p["id"], 0) <= now,
                 "cooldown_s": max(0, int(self._cool.get(p["id"], 0) - now)),
                 "ultimo": self._ultimo.get(p["id"])}
                for p in _providers()]

    def proxima(self):
        """(tipo, min, max) do gerador que será tentado primeiro, ou None."""
        for p in _providers():
            if self._cool.get(p["id"], 0) <= time.time():
                lo, hi = ETA.get(p["kind"], (2, 6))
                return p["kind"], lo, hi
        return None

    def _penalize(self, pid, seconds):
        with self._lock:
            self._cool[pid] = time.time() + seconds

    # ----------------------------------------------------- adaptadores
    def _wait(self, check, max_wait=None):
        """check() -> (done, resultado). Consulta até acabar o prazo."""
        deadline = time.time() + (max_wait or self.max_wait)
        while time.time() < deadline:
            done, res = check()
            if done:
                return res
            time.sleep(self.poll_every)
        raise VideoFailed("tempo esgotado")

    @staticmethod
    def _guard(r):
        if r.status_code in (401, 402, 403, 429):
            raise NoCredit(f"HTTP {r.status_code}")
        r.raise_for_status()

    def _pexels(self, p, prompt):
        """Banco de vídeos LIVRES (licença Pexels, uso comercial permitido). Não é vídeo
        gerado por IA: entra como último recurso para você nunca ficar sem vídeo."""
        termos = " ".join(re.findall(r"[A-Za-z]{3,}", prompt)[:8]) or "nature"
        r = requests.get("https://api.pexels.com/videos/search", timeout=20,
                         headers={"Authorization": p["key"]},
                         params={"query": termos, "orientation": "portrait",
                                 "size": "medium", "per_page": 8})
        self._guard(r)
        melhor = None
        for v in r.json().get("videos", []):
            for f in v.get("video_files", []):
                if f.get("file_type") == "video/mp4" and f.get("link"):
                    h = f.get("height") or 0
                    pontos = (1 if h >= f.get("width", 0) else 0, 1 if 720 <= h <= 1920 else 0)
                    if melhor is None or pontos > melhor[0]:
                        melhor = (pontos, f["link"])
            if melhor and melhor[0] == (1, 1):
                break
        if not melhor:
            raise VideoFailed("Pexels sem vídeo para esse tema")
        return melhor[1], None

    def _colab(self, p, prompt):
        """Seu Colab (GPU grátis). O notebook expõe /generate, /status/{id}, /video/{id}."""
        hdr = {"X-Token": p["key"]} if p["key"] else {}
        try:
            r = requests.post(f"{p['base']}/generate", headers=hdr, timeout=30,
                              json={"prompt": prompt[:500]})
        except requests.RequestException:
            COLAB["ts"] = 0.0            # túnel caiu: some da lista até se registrar de novo
            raise
        if r.status_code in (401, 403):
            raise NoCredit("COLAB_SECRET não confere com o do notebook")
        r.raise_for_status()
        job = r.json()["job_id"]

        def check():
            st = requests.get(f"{p['base']}/status/{job}", headers=hdr, timeout=20).json()
            if st.get("status") == "error":
                raise VideoFailed(str(st.get("error") or "erro no Colab")[:200])
            return st.get("status") == "done", None
        self._wait(check, max_wait=420)
        return f"{p['base']}/video/{job}", (hdr or {"X-Token": ""})

    def _tavus(self, p, prompt):
        """Avatar falando o roteiro (não é vídeo de cena genérico)."""
        hdr = {"x-api-key": p["key"], "Content-Type": "application/json"}
        body = {"replica_id": p.get("replica_id") or p["model"],
                "script": (prompt or "Hello from Ttoken Studio.")[:1500],
                "video_name": "ttoken-" + str(int(time.time()))}
        body.update(_extra("TAVUS_EXTRA_INPUT"))
        r = requests.post("https://tavusapi.com/v2/videos", headers=hdr, json=body, timeout=30)
        self._guard(r)
        data = r.json()
        vid = data.get("video_id")
        if not vid:
            raise VideoFailed("Tavus sem video_id")
        box = {}

        def check():
            st = requests.get(f"https://tavusapi.com/v2/videos/{vid}", headers=hdr,
                              timeout=20).json()
            status = (st.get("status") or "").lower()
            if status == "ready":
                box["url"] = st.get("download_url") or st.get("stream_url") or st.get("hosted_url")
                return True, None
            if status in ("error", "deleted"):
                raise VideoFailed(str(st.get("status_details") or status)[:200])
            return False, None
        self._wait(check)
        url = box.get("url") or data.get("hosted_url")
        if not url:
            raise VideoFailed("Tavus sem URL de vídeo")
        return url, None

    def _bigmodel(self, p, prompt):
        hdr = {"Authorization": f"Bearer {p['key']}", "Content-Type": "application/json"}
        body = {"model": p["model"], "prompt": (prompt or "")[:500]}
        body.update(_extra("BIGMODEL_EXTRA_INPUT"))
        r = requests.post("https://open.bigmodel.cn/api/paas/v4/videos/generations",
                          headers=hdr, json=body, timeout=30)
        self._guard(r)
        task_id = r.json().get("id")
        if not task_id:
            raise VideoFailed("sem task id do BigModel")
        box = {}

        def check():
            st = requests.get(f"https://open.bigmodel.cn/api/paas/v4/async-result/{task_id}",
                              headers=hdr, timeout=20).json()
            status = (st.get("task_status") or "").upper()
            if status == "SUCCESS":
                res = st.get("video_result") or []
                if res and isinstance(res[0], dict):
                    box["url"] = res[0].get("url")
                elif res and isinstance(res[0], str):
                    box["url"] = res[0]
                return True, None
            if status == "FAIL":
                raise VideoFailed(str(st.get("error") or "FAIL")[:200])
            return False, None
        self._wait(check)
        if not box.get("url"):
            raise VideoFailed("BigModel sem URL de vídeo")
        return box["url"], None

    def _agnes(self, p, prompt):
        """Agnes AI (API gratuita, compatível com OpenAI). A documentação pública não fixa os
        nomes dos campos de resposta, então aceitamos as variações conhecidas; se nada casar,
        o erro cai no próximo gerador da lista."""
        hdr = {"Authorization": f"Bearer {p['key']}", "Content-Type": "application/json"}
        body = {"model": p["model"], "prompt": (prompt or "")[:500],
                "width": 768, "height": 1152}                 # vertical 9:16
        body.update(_extra("AGNES_EXTRA_INPUT"))
        r = requests.post(f"{p['base']}/videos", headers=hdr, json=body, timeout=30)
        self._guard(r)
        j = r.json()
        vid = next((j.get(k) for k in ("videoId", "video_id", "task_id", "taskId", "id") if j.get(k)), None)
        if not vid:
            raise VideoFailed("Agnes sem id de tarefa")
        box = {}

        def achar_url(d):
            if not isinstance(d, dict):
                return None
            for k in ("video_url", "videoUrl", "url"):
                if isinstance(d.get(k), str) and d[k].startswith("http"):
                    return d[k]
            for k in ("content", "result", "output", "data"):
                v = d.get(k)
                if isinstance(v, list) and v:
                    v = v[0]
                u = achar_url(v) if isinstance(v, dict) else (v if isinstance(v, str) and v.startswith("http") else None)
                if u:
                    return u
            return None

        def check():
            rr = requests.get(f"{p['base']}/videos/{vid}", headers=hdr, timeout=20)
            if rr.status_code == 404:                       # rota nova de consulta da Agnes
                rr = requests.get(p["base"].rsplit("/v1", 1)[0] + "/agnesapi",
                                  headers=hdr, params={"video_id": vid}, timeout=20)
            self._guard(rr)
            st = rr.json()
            status = str(st.get("status") or st.get("state") or st.get("task_status") or "").lower()
            url = achar_url(st)
            if status in ("failed", "error", "cancelled", "canceled", "fail"):
                raise VideoFailed(str(st.get("error") or st.get("message") or status)[:200])
            if url and status in ("", "succeeded", "completed", "success", "done", "finished"):
                box["url"] = url
                return True, None
            return False, None
        self._wait(check, max_wait=max(self.max_wait, 360))
        if not box.get("url"):
            raise VideoFailed("Agnes sem URL de vídeo")
        return box["url"], None

    def _proxy(self, p, prompt):
        hdr = {"Authorization": f"Bearer {p['key']}"} if p["key"] else {}
        r = requests.post(f"{p['base']}/videos", headers=hdr, timeout=30,
                          json={"model": p["model"], "prompt": prompt})
        self._guard(r)
        job = r.json()["id"]

        def check():
            st = requests.get(f"{p['base']}/videos/{job}", headers=hdr, timeout=20).json()
            if st.get("status") == "failed":
                raise VideoFailed(st.get("error") or "falhou")
            return st.get("status") == "completed", None
        self._wait(check)
        return f"{p['base']}/videos/{job}/content", (hdr or None)

    def _magichour(self, p, prompt, imagem=None):
        hdr = {"Authorization": f"Bearer {p['key']}", "Content-Type": "application/json"}
        body = {"name": "ttoken", "model": p["model"], "aspect_ratio": "9:16",
                "end_seconds": int(os.environ.get("MAGIC_HOUR_SECONDS", "5")),
                "style": {"prompt": prompt}}
        if imagem:      # foto do produto -> vídeo (a imagem define o enquadramento)
            body.pop("aspect_ratio")
            body["assets"] = {"image_file_path": imagem}
        body.update(_extra("MAGIC_HOUR_EXTRA_INPUT"))
        url = "https://api.magichour.ai/v1/" + ("image-to-video" if imagem else "text-to-video")
        r = requests.post(url, headers=hdr, json=body, timeout=30)
        if r.status_code in (400, 422) and "aspect_ratio" in body:
            body.pop("aspect_ratio")        # nem todo modelo aceita 9:16
            r = requests.post(url, headers=hdr, json=body, timeout=30)
        self._guard(r)
        vid = r.json()["id"]
        box = {}

        def check():
            st = requests.get(f"https://api.magichour.ai/v1/video-projects/{vid}",
                              headers=hdr, timeout=20).json()
            if st.get("status") == "complete":
                box["url"] = next((d["url"] for d in st.get("downloads", []) if d.get("url")), None)
                return True, None
            if st.get("status") in ("error", "canceled"):
                raise VideoFailed(str((st.get("error") or {}).get("message") or st["status"])[:200])
            return False, None
        self._wait(check)
        if not box.get("url"):
            raise VideoFailed("resposta sem URL de vídeo")
        return box["url"], None

    def _mpt(self, p, prompt):
        auth = tuple(p["key"].split(":", 1)) if ":" in p["key"] else None
        r = requests.post(f"{p['base']}/api/v1/videos", auth=auth, timeout=30,
                          json={"video_subject": prompt, "video_aspect": "9:16",
                                "video_source": "pexels"})
        self._guard(r)
        j = r.json()
        task = (j.get("data") or j).get("task_id")
        if not task:
            raise VideoFailed("sem task_id")
        box = {}

        def check():
            j = requests.get(f"{p['base']}/api/v1/tasks/{task}", auth=auth, timeout=20).json()
            d = j.get("data") or j
            if d.get("state") == -1:
                raise VideoFailed("falha no MoneyPrinterTurbo")
            if d.get("state") == 1:
                box["url"] = next(iter(d.get("videos") or []), None)
                return True, None
            return False, None
        self._wait(check)
        if not box.get("url"):
            raise VideoFailed("resposta sem URL de vídeo")
        return box["url"], None

    def _fal(self, p, prompt, imagem=None):
        hdr = {"Authorization": f"Key {p['key']}", "Content-Type": "application/json"}
        modelo = p["model"]
        if imagem:      # foto -> vídeo (MiniMax Hailuo por padrão; pago por uso na fal.ai)
            modelo = os.environ.get("FAL_I2V_MODEL",
                                    "fal-ai/minimax/hailuo-02/standard/image-to-video")
            body = {"prompt": prompt, "image_url": imagem}
        else:
            body = {"prompt": prompt, "aspect_ratio": "9:16"}
            if "seedance" in modelo:
                body.update(duration="5", resolution="480p")
        body.update(_extra("FAL_EXTRA_INPUT"))
        r = requests.post(f"https://queue.fal.run/{modelo}", headers=hdr,
                          json=body, timeout=30)
        self._guard(r)
        sub = r.json()

        def check():
            st = requests.get(sub["status_url"], headers=hdr, timeout=20).json()
            if st.get("status") == "COMPLETED":
                if st.get("error"):
                    raise VideoFailed(str(st["error"])[:200])
                return True, None
            return False, None
        self._wait(check)
        res = requests.get(sub["response_url"], headers=hdr, timeout=30).json()
        url = (res.get("video") or {}).get("url") or \
              next((v.get("url") for v in res.get("videos", []) if v.get("url")), None)
        if not url:
            raise VideoFailed("resposta sem URL de vídeo")
        return url, None

    def _replicate(self, p, prompt):
        hdr = {"Authorization": f"Bearer {p['key']}", "Content-Type": "application/json",
               "Prefer": "wait=30"}
        inp = {"prompt": prompt}
        inp.update(_extra("REPLICATE_EXTRA_INPUT"))
        r = requests.post(f"https://api.replicate.com/v1/models/{p['model']}/predictions",
                          headers=hdr, json={"input": inp}, timeout=60)
        self._guard(r)
        pred = r.json()

        def check():
            nonlocal pred
            st = pred.get("status")
            if st in ("failed", "canceled"):
                raise VideoFailed(str(pred.get("error") or st)[:200])
            if st == "succeeded":
                return True, None
            pred = requests.get(pred["urls"]["get"], headers=hdr, timeout=20).json()
            return pred.get("status") == "succeeded", None
        self._wait(check)
        out = pred.get("output")
        if isinstance(out, dict):
            out = next((v for v in out.values() if isinstance(v, str) and v.startswith("http")), None)
        if isinstance(out, list):
            out = out[0] if out else None
        if not isinstance(out, str) or not out.startswith("http"):
            raise VideoFailed("resposta sem URL de vídeo")
        # arquivos servidos por api.replicate.com exigem o token no download
        needs_auth = "api.replicate.com" in out
        return out, ({"Authorization": f"Bearer {p['key']}"} if needs_auth else None)

    # ------------------------------------------------------------ geral
    def generate(self, prompt: str, imagem_url: str = ""):
        """Retorna (video_url, gerador, headers_de_download|None).
        Com `imagem_url`, só tenta geradores foto-para-vídeo (magichour, fal)."""
        provs = _providers()
        if imagem_url:
            provs = [p for p in provs if p["kind"] in I2V]
        if not provs:
            raise VideoFailed("Nenhum gerador de vídeo configurado "
                              "(FAL_KEY, REPLICATE_API_TOKEN ou VIDEO_PROVIDERS)")
        errors = []
        self.ultimo_erros = errors          # a pipeline mostra o motivo se cair num plano B
        for p in provs:
            if self._cool.get(p["id"], 0) > time.time():
                errors.append(f"{p['id']}: em cooldown")
                continue
            try:
                fn = getattr(self, "_" + p["kind"])
                if imagem_url:
                    url, dl_headers = fn(p, prompt, imagem_url)
                    self._evento(p["id"], True)
                    return url, p["id"] + "+foto", dl_headers
                url, dl_headers = fn(p, prompt)
                self._evento(p["id"], True)
                return url, p["id"], dl_headers
            except NoCredit as e:
                self._penalize(p["id"], 3600)
                errors.append(f"{p['id']}: sem crédito/chave ({e})")
                self._evento(p["id"], False, f"sem crédito/chave ({e})")
            except (requests.RequestException, KeyError, ValueError, VideoFailed) as e:
                self._penalize(p["id"], 120)
                errors.append(f"{p['id']}: {e}")
                self._evento(p["id"], False, str(e))
        raise VideoFailed("; ".join(errors))
