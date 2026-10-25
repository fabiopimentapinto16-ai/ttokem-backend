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
</style></head><body><h1>TTOKEM OS // PAINEL <a href="/sistema" style="font-size:13px;letter-spacing:0">sobre o sistema</a></h1>
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


SISTEMA = """<!DOCTYPE html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>TTOKEM OS - Sistema</title>
<style>
:root{--bg:#06070b;--card:#0f121b;--bd:#1e2536;--pr:#00e5b8;--se:#9d6bff;--tx:#eef1f8;--mu:#8089a6}
*{box-sizing:border-box;margin:0}body{background:var(--bg);color:var(--tx);font:16px/1.55 system-ui,sans-serif}
.w{max-width:1000px;margin:auto;padding:20px 16px 60px}h1{font-size:34px;line-height:1.15;margin:34px 0 10px}
h1 span{background:linear-gradient(90deg,var(--pr),var(--se));-webkit-background-clip:text;color:transparent}
h2{font-size:13px;letter-spacing:2px;text-transform:uppercase;color:var(--pr);margin:38px 0 12px;font-family:monospace}
p{color:var(--mu)}.g{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(210px,1fr))}
.c{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:14px}.c b{display:block;margin-bottom:4px}
.c small{color:var(--mu)}.k{border-color:var(--pr)}.tag{font:11px monospace;color:var(--se)}
table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:8px;border-bottom:1px solid var(--bd);text-align:left}
th{color:var(--mu);font-weight:500}.ok{color:var(--pr)}.no{color:#ff6b81}a{color:var(--pr)}
.b{display:inline-block;margin:10px 10px 0 0;padding:11px 16px;border-radius:8px;background:var(--pr);color:#000;font-weight:700;text-decoration:none}
.b.s{background:transparent;border:1px solid var(--bd);color:var(--tx)}
</style></head><body><div class="w">
<div class="tag">TTOKEM OS</div><h1>Um sistema aberto para <span>prompt, texto e vídeo</span></h1>
<p>Camadas simples, cada uma testada. Os modelos de IA (como o Beam, da Reflection) são o núcleo; o TTOKEM OS é a
camada de cima: escolhe a IA, comprime o pedido sem perder dados, gera o vídeo e entrega no app.</p>
<a class="b" href="/painel">Abrir o painel</a>
<a class="b s" href="https://github.com/fabiopimentapinto16-ai/ttokem-backend">Código no GitHub</a>
<h2>Camadas</h2><div class="g">
<div class="c k"><span class="tag">NÚCLEO</span><b>Maestro de IAs</b><small>Troca sozinho de IA quando uma acaba o crédito. Descobre os modelos disponíveis. Aceita Beam (Reflection), Groq, Gemini, OpenRouter, Cerebras, Mistral e Hugging Face.</small></div>
<div class="c"><span class="tag">COMPRESSÃO</span><b>Ttokem</b><small>Modo seguro (padrão) e agressivo. Auditoria que cancela a troca se um fato, número, verbo ou palavra de lógica sumir.</small></div>
<div class="c"><span class="tag">ÁRVORE</span><b>Rua Neural v2</b><small>Modelos prontos em hierarquia, com restrições herdadas. Só troca frases praticamente idênticas.</small></div>
<div class="c"><span class="tag">COMANDOS</span><b>/codigo /video /eficiente...</b><small>Escolhidos pela frase normal. Não alteram o seu texto.</small></div>
<div class="c"><span class="tag">VÍDEO</span><b>Maestro de vídeo</b><small>Vários geradores com plano B. O gerador nunca recebe o texto bruto.</small></div>
<div class="c"><span class="tag">APP</span><b>Ttoken Studio (Android)</b><small>Gera, legenda e compartilha. APK compilado pelo GitHub Actions.</small></div>
</div>
<h2>Ao vivo</h2><div class="c"><div id="vivo">Consultando o servidor...</div></div>
<h2>Comparação medida (50 prompts variados)</h2>
<div class="c"><table><tr><th>Motor</th><th>Economia</th><th>Dados intactos</th></tr>
<tr><td>T24 unificado</td><td>41,2%</td><td class="no">40/50</td></tr>
<tr><td>NHHC-4F</td><td>21,2%</td><td>47/50</td></tr>
<tr><td><b>Ttokem agressivo</b></td><td>34,2%</td><td class="ok">50/50</td></tr>
<tr><td>Ttokem seguro</td><td>0,8%</td><td class="ok">50/50</td></tr></table>
<p style="margin-top:10px;font-size:13px">Tokenizador aproximado e auditor próprio. Reproduza com <code>tests/benchmark_concorrentes.py</code>
(usa tiktoken no Colab). Os modelos prontos chegam a 95-98% só quando o prompt repete um modelo.</p></div>
</div><script>
fetch('/health').then(r=>r.json()).then(d=>{const a=d.ativos||{};
document.getElementById('vivo').innerHTML='Versão <b>'+d.versao+'</b><br>IAs de texto ativas: <b>'+((a.texto||[]).join(', ')||'nenhuma configurada')+
'</b><br>Geradores de vídeo: <b>'+((a.video||[]).join(', ')||'nenhum')+'</b>'}).catch(()=>{document.getElementById('vivo').textContent='Servidor indisponível agora.'});
</script></body></html>"""
