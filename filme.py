"""Junta as cenas do Modo Cineasta num único MP4 vertical (720x1280), no servidor.
Precisa do ffmpeg: usa o do sistema ou o do pacote imageio-ffmpeg. Sem ele, devolve erro claro
e as cenas continuam disponíveis separadas."""
import os
import shutil
import subprocess
import tempfile

import requests

MAX_MB = 80


class FilmeErro(Exception):
    pass


def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise FilmeErro("ffmpeg indisponível no servidor")


def _baixar(url, headers, destino):
    up = requests.get(url, headers=headers or None, stream=True, timeout=60)
    if up.status_code != 200:
        raise FilmeErro(f"origem respondeu {up.status_code}")
    total = 0
    with open(destino, "wb") as f:
        for pedaco in up.iter_content(65536):
            total += len(pedaco)
            if total > MAX_MB * 1_000_000:
                raise FilmeErro("cena grande demais")
            f.write(pedaco)


def juntar(cenas, saida, timeout=240):
    """cenas: lista de (url, headers|None) em ordem. Escreve o MP4 em `saida`."""
    if not cenas:
        raise FilmeErro("nenhuma cena para juntar")
    exe = _ffmpeg()
    pasta = tempfile.mkdtemp(prefix="filme_")
    try:
        arqs = []
        for i, (url, hdr) in enumerate(cenas):
            a = os.path.join(pasta, f"c{i}.mp4")
            _baixar(url, hdr, a)
            arqs.append(a)
        entradas = [x for a in arqs for x in ("-i", a)]
        norm = "".join(
            f"[{i}:v]scale=720:1280:force_original_aspect_ratio=decrease,"
            f"pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24[v{i}];" for i in range(len(arqs)))
        ligar = "".join(f"[v{i}]" for i in range(len(arqs))) + f"concat=n={len(arqs)}:v=1:a=0[out]"
        cmd = [exe, "-y", "-v", "error", *entradas, "-filter_complex", norm + ligar,
               "-map", "[out]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
               "-pix_fmt", "yuv420p", "-movflags", "+faststart", saida]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 or not os.path.exists(saida):
            raise FilmeErro("ffmpeg falhou: " + (r.stderr or "")[-200:])
        return saida
    except subprocess.TimeoutExpired:
        raise FilmeErro("a montagem demorou demais")
    finally:
        shutil.rmtree(pasta, ignore_errors=True)
