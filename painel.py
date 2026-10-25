"""Painel TTOKEM OS: mostra, para cada pedido, o caminho (comandos), a compressão real,
o cache real, a IA que respondeu e a latência. Está ligado ao app de vídeo (botão
"Gerar vídeo") e aos repositórios do GitHub. Usa o mesmo motor do app: nada de motor paralelo."""
import time, hashlib

import ttokem
import comandos

CACHE, CACHE_TTL = {}, 6 * 3600          # resposta de TEXTO (nunca de vídeo)
REPOS = [("App Android (APK)", "https://github.com/fabiopimentapinto16-ai/ttokem-studio-apk"),
         ("Backend (FastAPI)", "https://github.com/fabiopimentapinto16-ai/ttokem-backend")]


def processar(prompt: str, modo: str, llm, idioma: str = "português") -> dict:
    t0 = time.perf_counter()
    modo = "agressivo" if modo == "agressivo" else "auto"
    cmd = comandos.analisar(prompt)
    comp = ttokem.compress(cmd["texto"] or prompt, modo, cmd["tipo"], presets=True)
    enviado = comp["text"]
    base = {"comandos": cmd["mostrar"] or "(nenhum)", "tipo": cmd["tipo"] or "geral",
            "modo": comp["mode"], "tokens_antes": comp["original_tokens"],
            "tokens_depois": comp["compressed_tokens"], "economia_pct": comp["saving_pct"],
            "dados_intactos": bool(comp.get("semantica", {}).get("intacta", True))}
    chave = hashlib.sha1(f"{modo}|{idioma}|{cmd['mostrar']}|{enviado.lower()}".encode()).hexdigest()
    hit = CACHE.get(chave)
    if hit and hit[0] > time.time():
        base.update(cache="HIT", resposta=hit[1], ia=hit[2] + " (cache)")
    else:
        sistema = ("Você é o assistente do Ttoken Studio. Responda em " + idioma +
                   ", claro e prático." + (" " + cmd["instrucao"] if cmd["instrucao"] else ""))
        texto = ttokem.expandir_presets(enviado) if "@" in enviado else enviado
        resposta, ia = llm.chat([{"role": "system", "content": sistema},
                                 {"role": "user", "content": texto}], max_tokens=1200)
        CACHE[chave] = (time.time() + CACHE_TTL, resposta, ia)
        base.update(cache="MISS", resposta=resposta, ia=ia)
    base["latencia_ms"] = round((time.perf_counter() - t0) * 1000)
    base["repos"] = REPOS
    return base


HTML = """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>TTOKEM OS</title>
<style>
:root{--bg:#07080c;--card:#10131c;--bd:#1f2638;--pr:#00e5b8;--se:#9d6bff;--wr:#ffb020;--tx:#eef1f8;--mu:#7d86a3}
*{box-sizing:border-box;margin:0}body{background:var(--bg);color:var(--tx);font:15px system-ui,sans-serif;padding:16px;max-width:1000px;margin:auto}
h1{font-size:20px;letter-spacing:2px;color:var(--pr);margin:6px 0 16px}
.g{display:grid;gap:12px;grid-template-columns:1fr 1fr}@media(max-width:700px){.g{grid-template-columns:1fr}}
.c{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px}
.l{font:11px monospace;color:var(--mu);text-transform:uppercase;letter-spacing:1px;margin-bottom:6px}
.v{font:600 15px monospace}textarea{width:100%;height:110px;background:#000;color:#fff;border:1px solid var(--bd);border-radius:8px;padding:10px;font:inherit}
button{padding:12px 14px;border:0;border-radius:8px;font-weight:700;cursor:pointer;background:var(--pr);color:#000;margin:8px 8px 0 0}
button.s{background:var(--se);color:#fff}button:disabled{opacity:.5}label{color:var(--mu);font-size:13px}
pre{white-space:pre-wrap;word-wrap:break-word;font:13px/1.6 monospace;color:#ccd}a{color:var(--pr)}.ok{color:var(--pr)}.no{color:#ff4d6d}
</style></head><body><h1>TTOKEM OS // PAINEL</h1>
<div class="g"><div class="c" style="grid-column:1/-1"><div class="l">Escreva normal: o comando é escolhido sozinho</div>
<textarea id="p" placeholder="Ex: eu quero com eficiência esse código de login em Kivy"></textarea>
<label><input type="checkbox" id="ag"> modo agressivo (economiza mais; dados continuam auditados)</label><br>
<button id="b" onclick="run()">Executar</button><button class="s" id="v" onclick="video()">Gerar vídeo com esta ideia</button></div>
<div class="c"><div class="l">Caminho (comandos)</div><div class="v" id="m1">-</div></div>
<div class="c"><div class="l">Compressão</div><div class="v" id="m2">-</div></div>
<div class="c"><div class="l">Cache de resposta (texto)</div><div class="v" id="m3">-</div></div>
<div class="c"><div class="l">IA e latência</div><div class="v" id="m4">-</div></div>
<div class="c" style="grid-column:1/-1"><div class="l">Resposta</div><pre id="o">-</pre></div>
<div class="c" style="grid-column:1/-1"><div class="l">Vídeo</div><div id="vd" class="v">-</div></div>
<div class="c" style="grid-column:1/-1"><div class="l">GitHub</div><div id="gh" class="v">
<a href="https://github.com/fabiopimentapinto16-ai/ttokem-studio-apk">App Android (APK)</a> ·
<a href="https://github.com/fabiopimentapinto16-ai/ttokem-backend">Backend</a></div></div></div>
<script>
const $=i=>document.getElementById(i);
async function run(){const p=$('p').value.trim();if(!p)return;$('b').disabled=true;$('o').textContent='...';
try{const r=await fetch('/api/painel',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({prompt:p,modo:$('ag').checked?'agressivo':'auto'})});const d=await r.json();
if(!r.ok)throw new Error(d.detail||r.status);
$('m1').textContent=d.comandos+' | '+d.tipo;
$('m2').innerHTML=d.tokens_antes+' -> '+d.tokens_depois+' tokens ('+d.economia_pct+'%) <span class="'+(d.dados_intactos?'ok">dados intactos':'no">verificar')+'</span>';
$('m3').textContent=d.cache+(d.cache==='HIT'?' (sem gastar IA)':' (novo)');
$('m4').textContent=d.ia+' | '+d.latencia_ms+' ms';$('o').textContent=d.resposta;
}catch(e){$('o').textContent='Erro: '+e.message}finally{$('b').disabled=false}}
async function video(){const p=$('p').value.trim();if(p.length<3)return;$('v').disabled=true;$('vd').textContent='enviando...';
try{const r=await fetch('/api/gerar',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({ideia:p,economia:$('ag').checked?'agressivo':'auto'})});const d=await r.json();
if(!r.ok)throw new Error(d.detail||r.status);const id=d.job_id;
for(let i=0;i<120;i++){await new Promise(s=>setTimeout(s,5000));const s=await (await fetch('/api/status/'+id)).json();
$('vd').textContent=(s.etapa||s.status);if(s.status==='pronto'){$('vd').innerHTML=s.video_url?'<a href="'+s.video_url+'">Abrir vídeo</a>':'pronto (só legenda)';
if(s.texto)$('o').textContent=s.texto;break}if(s.status==='erro'){$('vd').textContent='Erro: '+(s.erro||'');break}}
}catch(e){$('vd').textContent='Erro: '+e.message}finally{$('v').disabled=false}}
</script></body></html>"""
