"""Compilar pelo Git: envia código para um BRANCH de teste do repositório do app e
dispara o GitHub Actions. O branch principal nunca é tocado (o app que funciona
fica seguro). Variáveis: GITHUB_TOKEN (escopo: só o repo do app, Contents + Actions),
BUILD_REPO (padrão: fabiopimentapinto16-ai/ttokem-studio-apk), BUILD_BRANCH
(padrão: ia-build), BUILD_SECRET (senha do app para usar estes endpoints)."""
import base64
import io
import os
import time
import zipfile

import requests

API = "https://api.github.com"
WORKFLOW = "build.yml"


class GitError(Exception):
    pass


def cfg():
    return {"token": (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip(),
            "repo": os.environ.get("BUILD_REPO", "fabiopimentapinto16-ai/ttokem-studio-apk").strip(),
            "branch": os.environ.get("BUILD_BRANCH", "ia-build").strip(),
            "secret": os.environ.get("BUILD_SECRET", "")}


def _h(c):
    return {"Authorization": f"Bearer {c['token']}", "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ttoken-studio"}


def _req(method, path, c, **kw):
    r = requests.request(method, f"{API}/repos/{c['repo']}{path}", headers=_h(c), timeout=30, **kw)
    if r.status_code >= 400:
        raise GitError(f"GitHub {r.status_code}: {r.text[:160]}")
    return r


def seguro(path: str) -> bool:
    p = path.replace("\\", "/")
    return (bool(p) and not p.startswith(("/", ".git")) and ".." not in p.split("/")
            and not p.lower().startswith(".github") and len(p) <= 120)


def garantir_branch(c=None):
    c = c or cfg()
    ref = requests.get(f"{API}/repos/{c['repo']}/git/ref/heads/{c['branch']}", headers=_h(c), timeout=30)
    if ref.status_code == 200:
        return
    base = _req("GET", "/git/ref/heads/main", c).json()["object"]["sha"]
    _req("POST", "/git/refs", c, json={"ref": f"refs/heads/{c['branch']}", "sha": base})


def enviar_arquivo(path, conteudo: str, mensagem="Código gerado no Ttoken Studio", c=None):
    c = c or cfg()
    if not seguro(path):
        raise GitError("caminho de arquivo não permitido")
    garantir_branch(c)
    antigo = requests.get(f"{API}/repos/{c['repo']}/contents/{path}", headers=_h(c),
                          params={"ref": c["branch"]}, timeout=30)
    body = {"message": mensagem[:100], "branch": c["branch"],
            "content": base64.b64encode(conteudo.encode("utf-8")).decode()}
    if antigo.status_code == 200:
        body["sha"] = antigo.json()["sha"]
    out = _req("PUT", f"/contents/{path}", c, json=body).json()
    return out["commit"]["sha"][:7]


def iniciar_build(c=None):
    """Dispara o workflow no branch de teste e devolve o run_id."""
    c = c or cfg()
    garantir_branch(c)
    t0 = time.time()
    _req("POST", f"/actions/workflows/{WORKFLOW}/dispatches", c, json={"ref": c["branch"]})
    for _ in range(10):                    # o run demora um instante para aparecer
        time.sleep(2)
        runs = _req("GET", f"/actions/workflows/{WORKFLOW}/runs", c,
                    params={"branch": c["branch"], "event": "workflow_dispatch",
                            "per_page": 5}).json().get("workflow_runs", [])
        for r in runs:
            if r["created_at"] >= time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t0 - 5)):
                return r["id"]
    raise GitError("build disparado, mas o run não apareceu (veja a aba Actions)")


def status(run_id, c=None):
    c = c or cfg()
    r = _req("GET", f"/actions/runs/{int(run_id)}", c).json()
    return {"status": r["status"], "conclusao": r.get("conclusion"), "url": r["html_url"]}


def baixar_apk(run_id, c=None):
    """Baixa o artifact do build e devolve (nome, bytes do .apk)."""
    c = c or cfg()
    arts = _req("GET", f"/actions/runs/{int(run_id)}/artifacts", c).json().get("artifacts", [])
    art = next((a for a in arts if a["name"] == "ttoken-apk"), None)
    if not art:
        raise GitError("este build não tem APK (ainda rodando ou falhou)")
    z = _req("GET", f"/actions/artifacts/{art['id']}/zip", c)
    with zipfile.ZipFile(io.BytesIO(z.content)) as zf:
        nome = next((n for n in zf.namelist() if n.endswith(".apk")), None)
        if not nome:
            raise GitError("APK não encontrado dentro do artifact")
        return os.path.basename(nome), zf.read(nome)
