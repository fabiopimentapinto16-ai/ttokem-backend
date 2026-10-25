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
    modo = modo if modo in ("auto", "light", "agressivo", "v24") else "auto"
    cmd = comandos.analisar(prompt)
    comp = ttokem.compress(cmd["texto"] or prompt, modo, cmd["tipo"], presets=True)
    enviado = comp["text"]
    if comp.get("refusal"):               # v24 recusou (perderia dado): manda o texto original
        enviado = cmd["texto"] or prompt
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



def sistema_info(rate_limit=None) -> dict:
    """Parâmetros reais do motor (lidos do código, nunca escritos à mão na página)."""
    import rua_neural, maestro, pipeline
    arv = []
    def anda(no, prof):
        arv.append({"nome": no.nome, "nivel": no.nivel, "prof": prof,
                    "resumo": no.resumo if no.nivel == "MODEL" else "",
                    "frases": len(no.frases), "regras_proprias": len(no.regras),
                    "regras_herdadas": len(no.regras_herdadas())})
        for f in no.filhos:
            anda(f, prof + 1)
    anda(rua_neural.BIBLIOTECA.raiz, 0)
    return {
        "arvore": arv,
        "parametros": {
            "Economia mínima para comprimir (MIN_SAVING)": f"{ttokem.MIN_SAVING:.0%}",
            "Similaridade mínima para trocar frase por modelo (Rua Neural)": f"{rua_neural.LIMITE:.0%}",
            "Texto longo para o codebook V24 (caracteres)": ttokem.MIN_CHARS_V24,
            "Modos de compressão": "auto (seguro, padrão) | light | v24 | agressivo",
            "Cache de resposta de texto (painel)": f"{CACHE_TTL // 3600} h",
            "Cache de roteiro/legenda (pipeline)": f"{pipeline.LLM_CACHE_TTL // 3600} h",
            "Limite de pedidos por IP": f"{rate_limit}/hora" if rate_limit else "configurável",
            "Máximo de comandos por pedido": comandos.MAX_COMANDOS,
            "Idioma padrão": pipeline.IDIOMA_PADRAO,
        },
        "auditor": ["fatos: e-mails, URLs, #ids e tokens com número ou _ não podem sumir",
                    "lógica: not/no/never/only/or/but/if, não/nunca/apenas/somente/ou/mas/se/sem mantêm a contagem",
                    "verbos de ação (create, build, gere, crie...) não podem sumir",
                    "restrições herdadas pela árvore (9:16, 5 s, \"sem texto\", versões...) mantêm a contagem",
                    "em qualquer dúvida o motor devolve o texto original (fail-closed)"],
        "regras_por_ramo": {k: len(v) for k, v in rua_neural.REGRAS.items()},
        "dicionarios": {k: len(v) for k, v in ttokem._LIVROS.items()},
        "nhhc4f": {f: [{"tipos": list(t), "de": rx, "para": novo} for t, rx, novo in itens]
                   for f, itens in ttokem.NHHC4F.items()},
        "modelos_prontos": {k: {"tipo": v["tipo"], "frases": len(v["frases"])}
                            for k, v in ttokem.PRESETS.items()},
        "comandos": comandos.catalogo(),
        "provedores_texto": list(maestro.PROVIDERS),
    }


MOTORES = [   # números MEDIDOS (tests/benchmark_concorrentes.py, tokenizador aproximado)
    {"id": "auto", "nome": "Seguro (padrão)", "economia": "0,8% (variados) · até 15% em código conhecido",
     "risco": "nenhum medido", "perda": "50/50 prompts intactos",
     "quando": "Sempre que a qualidade dos dados vem primeiro."},
    {"id": "agressivo", "nome": "Agressivo", "economia": "34% (variados) · 37% em código conhecido",
     "risco": "baixo (telegráfico, auditado)", "perda": "50/50 prompts intactos",
     "quando": "Prompts longos em que o estilo telegráfico não atrapalha."},
    {"id": "light", "nome": "Leve", "economia": "0,8%", "risco": "nenhum medido",
     "perda": "50/50 prompts intactos", "quando": "Só limpeza de enchimento."},
    {"id": "v24", "nome": "Codebook V24", "economia": "alta só em tickets/ocorrências longos",
     "risco": "ALTO: usa códigos (§ID) e perdeu dados em 31 de 50 prompts",
     "perda": "19/50 prompts intactos",
     "quando": "Só textos longos de ocorrência. Evite em pedidos de vídeo, imagem ou código."},
]


def biblioteca() -> dict:
    """Motores e modelos prontos (@id) para a pessoa escolher na página."""
    modelos = []
    for pid, pre in ttokem.PRESETS.items():
        cheio = " ".join(pre["frases"])
        curto = "@" + pid
        modelos.append({"id": pid, "tipo": pre["tipo"], "frases": len(pre["frases"]),
                        "prompt": cheio, "referencia": curto,
                        "tokens_cheio": ttokem.est_tokens(cheio),
                        "tokens_curto": ttokem.est_tokens(curto),
                        "economia": f"{(1 - ttokem.est_tokens(curto) / ttokem.est_tokens(cheio)):.0%}"})
    return {"motores": MOTORES, "modelos": modelos,
            "aviso": "Os 95-98% valem só para modelos prontos: a referência é expandida de volta "
                     "ANTES da IA, então nada se perde. Códigos tipo V24 podem danificar a mensagem."}

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
<label>Motor: <select id="md" style="background:#000;color:#fff;border:1px solid var(--bd);border-radius:6px;padding:6px">
<option value="auto">Seguro (padrão)</option><option value="agressivo">Agressivo (economiza mais, auditado)</option>
<option value="light">Leve</option><option value="v24">Codebook V24 (risco: pode danificar a mensagem)</option></select></label><br>
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
(function(){const q=new URLSearchParams(location.search);if(q.get('texto'))$('p').value=q.get('texto');if(q.get('modo'))$('md').value=q.get('modo')})();
async function run(){const p=$('p').value.trim();if(!p)return;$('b').disabled=true;$('o').textContent='...';
try{const r=await fetch('/api/painel',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({prompt:p,modo:$('md').value})});const d=await r.json();
if(!r.ok)throw new Error(d.detail||r.status);
$('m1').textContent=d.comandos+' | '+d.tipo;
$('m2').innerHTML=d.tokens_antes+' -> '+d.tokens_depois+' tokens ('+d.economia_pct+'%) <span class="'+(d.dados_intactos?'ok">dados intactos':'no">verificar')+'</span>';
$('m3').textContent=d.cache+(d.cache==='HIT'?' (sem gastar IA)':' (novo)');
$('m4').textContent=d.ia+' | '+d.latencia_ms+' ms';$('o').textContent=d.resposta;
}catch(e){$('o').textContent='Erro: '+e.message}finally{$('b').disabled=false}}
async function video(){const p=$('p').value.trim();if(p.length<3)return;$('v').disabled=true;$('vd').textContent='enviando...';
try{const r=await fetch('/api/gerar',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({ideia:p,economia:($('md').value==='agressivo'?'agressivo':'auto')})});const d=await r.json();
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
<h2>Como funciona a Rua Neural</h2><div class="c" id="como">
<ol style="margin-left:18px;color:var(--mu)">
<li><b style="color:var(--tx)">Raiz → Contexto → Tarefa → Modelo.</b> A tarefa (imagem, vídeo ou código) vem do comando ou das pistas da frase.</li>
<li><b style="color:var(--tx)">Seleção em sequência.</b> Cada frase do pedido só é comparada com os modelos DAQUELA tarefa.</li>
<li><b style="color:var(--tx)">Troca só se for praticamente idêntica:</b> mesma lógica (não, ou, se...), mesmos fatos (números, ids, e-mails) e as mesmas palavras de conteúdo.</li>
<li><b style="color:var(--tx)">Herança de restrições.</b> O que um nó pai exige (ex.: 9:16, "sem texto"), o filho herda. Se o texto final perder algo, a troca é cancelada.</li>
<li><b style="color:var(--tx)">Referência curta (@modelo) expandida antes da IA.</b> Nada se perde: o gerador recebe o texto completo.</li>
<li><b style="color:var(--tx)">Sem IA e sem dependências:</b> determinístico, roda no celular e no servidor grátis.</li></ol></div>
<h2>A árvore (lida do motor ao vivo)</h2><div class="c"><div id="arv" style="font:13px/1.8 monospace">...</div></div>
<h2>Parâmetros</h2><div class="c"><table id="par"></table></div>
<h2>Auditoria de dados (fail-closed)</h2><div class="c"><ul id="aud" style="margin-left:18px;color:var(--mu)"></ul></div>
<h2>NHHC-4F: 4 fatores como equivalências</h2><div class="c"><table id="nh"></table>
<p style="margin-top:8px;font-size:13px">Só linguagem natural. Cada troca passa pelo auditor acima.</p></div>
<h2>Dicionários, modelos prontos e comandos</h2><div class="c"><div id="dic"></div></div>
<h2>Biblioteca: escolha o motor e o prompt</h2>
<div class="c"><p id="aviso" style="margin-bottom:10px"></p><div class="g" id="mot"></div></div>
<div class="g" id="mod" style="margin-top:10px"></div>
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

fetch('/api/sistema').then(r=>r.json()).then(s=>{
const E=x=>String(x).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
document.getElementById('arv').innerHTML=s.arvore.map(n=>'<div style="padding-left:'+(n.prof*18)+'px"><span class="'+(n.nivel==='MODEL'?'ok':'')+'">'+E(n.nome)+'</span> <small style="color:var(--mu)">'+n.nivel+(n.nivel==='MODEL'?' · '+n.frases+' frases · '+E(n.resumo):' · herda '+n.regras_herdadas+' regras')+'</small></div>').join('');
document.getElementById('par').innerHTML=Object.entries(s.parametros).map(([k,v])=>'<tr><td>'+E(k)+'</td><td><b>'+E(v)+'</b></td></tr>').join('');
document.getElementById('aud').innerHTML=s.auditor.map(a=>'<li>'+E(a)+'</li>').join('');
let nh='';for(const [f,it] of Object.entries(s.nhhc4f)){for(const x of it){nh+='<tr><td>'+f+'</td><td><code>'+E(x.de)+'</code></td><td>'+(x.para?E(x.para):'(remove)')+'</td></tr>'}}
document.getElementById('nh').innerHTML='<tr><th>Fator</th><th>Frase</th><th>Vira</th></tr>'+nh;
document.getElementById('dic').innerHTML='Dicionários por tipo: '+Object.entries(s.dicionarios).map(([k,v])=>k+' ('+v+')').join(', ')+
'<br>Modelos prontos: '+Object.entries(s.modelos_prontos).map(([k,v])=>k+' ['+v.tipo+', '+v.frases+' frases]').join(', ')+
'<br>Comandos: '+s.comandos.map(c=>c.comando).join(' ')+'<br>IAs de texto suportadas: '+s.provedores_texto.join(', ');
}).catch(()=>{document.getElementById('arv').textContent='Servidor indisponível agora.'});

fetch('/api/prompts').then(r=>r.json()).then(b=>{
const E=x=>String(x).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('aviso').textContent=b.aviso;
document.getElementById('mot').innerHTML=b.motores.map(m=>'<div class="c"><b>'+E(m.nome)+'</b><small>Economia: '+E(m.economia)+'<br>Risco: <span class="'+(m.risco.startsWith('ALTO')?'no':'ok')+'">'+E(m.risco)+'</span><br>'+E(m.perda)+'<br>'+E(m.quando)+'</small><br><a class="b s" href="/painel?modo='+m.id+'">Usar este motor</a></div>').join('');
window._mods=b.modelos;
document.getElementById('mod').innerHTML=b.modelos.map((m,i)=>'<div class="c"><span class="tag">MODELO PRONTO · '+E(m.tipo)+'</span><b>'+E(m.id)+'</b><small>'+m.frases+' frases · '+m.tokens_cheio+' → '+m.tokens_curto+' tokens ('+m.economia+' menos, sem perda)</small><pre style="white-space:pre-wrap;font:12px/1.5 monospace;color:#aab;max-height:120px;overflow:auto;margin-top:6px">'+E(m.prompt)+'</pre>'+
'<button class="b s" data-i="'+i+'" data-k="p">Copiar prompt completo</button><button class="b s" data-i="'+i+'" data-k="r">Copiar '+E(m.referencia)+'</button><a class="b" href="/painel?texto='+encodeURIComponent(m.referencia)+'">Usar no painel</a></div>').join('');
document.querySelectorAll('button[data-k]').forEach(bt=>bt.onclick=()=>{const m=window._mods[bt.dataset.i];
navigator.clipboard.writeText(bt.dataset.k==='p'?m.prompt:m.referencia).then(()=>{bt.textContent='Copiado!'}).catch(()=>{bt.textContent='Selecione e copie manualmente'})});
}).catch(()=>{});
fetch('/health').then(r=>r.json()).then(d=>{const a=d.ativos||{};
document.getElementById('vivo').innerHTML='Versão <b>'+d.versao+'</b><br>IAs de texto ativas: <b>'+((a.texto||[]).join(', ')||'nenhuma configurada')+
'</b><br>Geradores de vídeo: <b>'+((a.video||[]).join(', ')||'nenhum')+'</b>'}).catch(()=>{document.getElementById('vivo').textContent='Servidor indisponível agora.'});
</script></body></html>"""
