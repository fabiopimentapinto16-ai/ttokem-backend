"""Painel TTOKEM OS: mostra, para cada pedido, o caminho (comandos), a compressão real,
o cache real, a IA que respondeu e a latência. Está ligado ao app de vídeo (botão
"Gerar vídeo") e aos repositórios do GitHub. Usa o mesmo motor do app: nada de motor paralelo."""
import time, hashlib

import ttokem
import comandos

CACHE, CACHE_TTL = {}, 6 * 3600          # resposta de TEXTO (nunca de vídeo)
REPOS = [("App Android (APK)", "https://github.com/fabiopimentapinto16-ai/ttokem-studio-apk"),
         ("Backend (FastAPI)", "https://github.com/fabiopimentapinto16-ai/ttokem-backend")]


def processar(prompt: str, modo: str, llm, idioma: str = "português", arena: int = 0) -> dict:
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
    arena = arena if arena >= 2 else 0
    chave = hashlib.sha1(f"{modo}|{idioma}|{cmd['mostrar']}|{arena}|{enviado.lower()}".encode()).hexdigest()
    hit = CACHE.get(chave)
    if hit and hit[0] > time.time():
        base.update(cache="HIT", resposta=hit[1], ia=hit[2] + " (cache)")
    else:
        sistema = ("Você é o assistente do Ttoken Studio. Responda em " + idioma +
                   ", claro e prático." + (" " + cmd["instrucao"] if cmd["instrucao"] else ""))
        texto = ttokem.expandir_presets(enviado) if "@" in enviado else enviado
        msgs = [{"role": "system", "content": sistema}, {"role": "user", "content": texto}]
        if arena:
            resposta, ia, det = llm.arena(msgs, n=arena, max_tokens=1200)
            base["arena"] = det
        else:
            resposta, ia = llm.chat(msgs, max_tokens=1200)
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
        "provedores_texto": list(maestro.provedores()),
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
<option value="light">Leve</option><option value="v24">Codebook V24 (risco: pode danificar a mensagem)</option></select></label>
<label style="margin-left:8px">Arena: <select id="ar" style="background:#000;color:#fff;border:1px solid var(--bd);border-radius:6px;padding:6px">
<option value="0">Normal</option><option value="3">Melhor de 3 (gasta 3x a cota)</option><option value="5">Melhor de 5</option></select></label><br>
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
body:JSON.stringify({prompt:p,modo:$('md').value,arena:parseInt($('ar').value)})});const d=await r.json();
if(!r.ok)throw new Error(d.detail||r.status);
$('m1').textContent=d.comandos+' | '+d.tipo;
$('m2').innerHTML=d.tokens_antes+' -> '+d.tokens_depois+' tokens ('+d.economia_pct+'%) <span class="'+(d.dados_intactos?'ok">dados intactos':'no">verificar')+'</span>';
$('m3').textContent=d.cache+(d.cache==='HIT'?' (sem gastar IA)':' (novo)');
$('m4').textContent=d.ia+' | '+d.latencia_ms+' ms'+(d.arena?' | arena: '+d.arena.candidatas+' candidatas, juiz '+d.arena.juiz:'');$('o').textContent=d.resposta;
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
.lab{display:grid;gap:12px;grid-template-columns:1.2fr 1fr}@media(max-width:700px){.lab{grid-template-columns:1fr}}
.lab textarea{width:100%;height:110px;background:#000;color:#fff;border:1px solid var(--bd);border-radius:8px;padding:10px;font:inherit;margin:8px 0}
.lab select{background:#000;color:#fff;border:1px solid var(--bd);border-radius:6px;padding:9px}.res{margin-top:12px;font:14px/1.6 monospace;color:var(--mu);word-wrap:break-word}
.sq canvas{width:100%;max-width:360px;aspect-ratio:1;display:block;margin:6px auto;background:#04050a;border-radius:10px;border:1px solid var(--bd)}
.l{font:11px monospace;color:var(--mu);text-transform:uppercase;letter-spacing:1px}
code{word-break:break-all}.c{min-width:0;overflow-x:auto}
canvas.m3d{display:block;width:100%;height:120px;margin:-4px 0 8px;background:radial-gradient(ellipse at 50% 40%,#10172a,#04050a);border-radius:8px;border:1px solid var(--bd)}
</style></head><body><div class="w">
<div class="tag">TTOKEM OS</div><h1>Um sistema aberto para <span>prompt, texto e vídeo</span></h1>
<p>Camadas simples, cada uma testada. Os modelos de IA (como o Beam, da Reflection) são o núcleo; o TTOKEM OS é a
camada de cima: escolhe a IA, comprime o pedido sem perder dados, gera o vídeo e entrega no app.</p>
<a class="b" href="/painel">Abrir o painel</a>
<a class="b s" href="https://github.com/fabiopimentapinto16-ai/ttokem-backend">Código no GitHub</a>
<h2>Laboratório ao vivo</h2>
<div class="lab"><div class="c"><div class="l">Digite qualquer frase e veja o corte</div>
<textarea id="lt" placeholder="Ex: Create a high-performance python script using fastapi that compiles into an android apk via buildozer."></textarea>
<select id="lm"><option value="auto">Seguro (padrão)</option><option value="agressivo">Agressivo</option><option value="light">Leve</option></select>
<button class="b" id="lb" style="border:0;cursor:pointer">Comprimir</button>
<div id="lr" class="res">Aguardando...</div></div>
<div class="c sq"><div class="l">Fluxo do motor (loop)</div><canvas id="cv" width="360" height="360"></canvas>
<small id="cvl" style="color:var(--mu)">Entrada → fatores → Rua Neural → auditoria → saída</small></div></div>
<h2>Planos (estrutura)</h2><div class="g">
<div class="c"><span class="tag">STARTER</span><b>Grátis com o APK</b><small>Limite de uso de 50% da capacidade. Modo seguro e agressivo auditados.</small></div>
<div class="c k"><span class="tag">PRO</span><b>R$ 10 (Brasil) · US$ 2 (exterior)</b><small>Sem o limite do Starter. Cobrança ainda não ativada: esta é só a estrutura planejada.</small></div></div>
<h2>Camadas</h2><div class="g">
<div class="c k"><canvas class="m3d" data-m="maestro" aria-hidden="true"></canvas><span class="tag">NÚCLEO</span><b>Maestro de IAs</b><small>Troca sozinho de IA quando uma acaba o crédito. Descobre os modelos disponíveis. Aceita Beam (Reflection), Groq, Gemini, OpenRouter, Cerebras, Mistral, Hugging Face e qualquer API compatível com OpenAI (provedor genérico).</small></div><div class="c"><canvas class="m3d" data-m="arena" aria-hidden="true"></canvas><span class="tag">ARENA</span><b>Melhor de 3 a 5</b><small>Várias IAs respondem a mesma tarefa e uma julga. Opcional: gasta de 3 a 5 vezes a cota grátis.</small></div>
<div class="c"><canvas class="m3d" data-m="compressao" aria-hidden="true"></canvas><span class="tag">COMPRESSÃO</span><b>Ttokem</b><small>Modo seguro (padrão) e agressivo. Auditoria que cancela a troca se um fato, número, verbo ou palavra de lógica sumir.</small></div>
<div class="c"><canvas class="m3d" data-m="arvore" aria-hidden="true"></canvas><span class="tag">ÁRVORE</span><b>Rua Neural v2</b><small>Modelos prontos em hierarquia, com restrições herdadas. Só troca frases praticamente idênticas.</small></div>
<div class="c"><canvas class="m3d" data-m="comandos" aria-hidden="true"></canvas><span class="tag">COMANDOS</span><b>/codigo /video /eficiente...</b><small>Escolhidos pela frase normal. Não alteram o seu texto.</small></div>
<div class="c"><canvas class="m3d" data-m="video" aria-hidden="true"></canvas><span class="tag">VÍDEO</span><b>Maestro de vídeo</b><small>Vários geradores com plano B. O gerador nunca recebe o texto bruto.</small></div>
<div class="c"><canvas class="m3d" data-m="app" aria-hidden="true"></canvas><span class="tag">APP</span><b>Ttoken Studio (Android)</b><small>Gera, legenda e compartilha. APK compilado pelo GitHub Actions.</small></div>
<div class="c"><canvas class="m3d" data-m="cofre" aria-hidden="true"></canvas><span class="tag">COFRE</span><b>Cofre de moedas</b><small>Moedas grátis que você não usa no dia ficam guardadas no cofre (até 30 dias). Moedas compradas nunca expiram.</small></div>
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


(function(){
const $=i=>document.getElementById(i),E=x=>String(x).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
$('lb').onclick=async()=>{const t=$('lt').value.trim();if(!t)return;$('lb').disabled=true;$('lr').textContent='...';
try{const r=await fetch('/api/comprimir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({texto:t,modo:$('lm').value})});
const d=await r.json();if(!r.ok)throw new Error(d.detail||r.status);const ok=d.semantica&&d.semantica.intacta!==false;
$('lr').innerHTML='Antes: <b>'+d.original_tokens+'</b> tokens<br>Depois: <b>'+d.compressed_tokens+'</b> tokens<br>Economia: <b class="'+(d.saving_pct>0?'ok':'')+'">'+d.saving_pct+'%</b> · dados <b class="'+(ok?'ok':'no')+'">'+(ok?'intactos':'verificar')+'</b><br>Comandos: '+E(d.comandos||'(nenhum)')+'<br><br>'+E(d.text);
if(window._pulso)window._pulso();}catch(e){$('lr').textContent='Erro: '+e.message}finally{$('lb').disabled=false}};
// canvas: malha ROLE/CONTEXT/TASK/CONSTRAINTS -> Rua Neural -> Auditoria -> Saida (loop)
const cv=$('cv'),cx=cv.getContext('2d'),W=360,H=360;
const N=[{n:'Entrada',x:180,y:30},{n:'ROLE',x:60,y:110},{n:'CONTEXT',x:140,y:110},{n:'TASK',x:220,y:110},{n:'CONSTRAINTS',x:300,y:110},
{n:'Rua Neural',x:180,y:200},{n:'Auditoria',x:180,y:275},{n:'Saída',x:180,y:335},{n:'Merkle (planejado)',x:315,y:260,plan:1}];
const Ed=[[0,1],[0,2],[0,3],[0,4],[1,5],[2,5],[3,5],[4,5],[5,6],[6,7],[6,8]];
let pulsos=[],ultimo=0,visivel=true,reduz=matchMedia('(prefers-reduced-motion: reduce)').matches;
function novo(){const e=Ed[Math.floor(Math.random()*4)];pulsos.push({e:e,t:0,fila:[[5,6],[6,7]]})}
window._pulso=()=>{for(let i=0;i<4;i++)pulsos.push({e:Ed[i],t:-i*.08,fila:[[5,6],[6,7]]})};
function seg(e){return [N[e[0]],N[e[1]]]}
function desenha(ts){if(!visivel){requestAnimationFrame(desenha);return}
cx.clearRect(0,0,W,H);cx.lineWidth=1;
Ed.forEach(e=>{const[a,b]=seg(e);cx.strokeStyle=(a.plan||b.plan)?'#3a3358':'#1e2a40';cx.setLineDash((a.plan||b.plan)?[4,4]:[]);cx.beginPath();cx.moveTo(a.x,a.y);cx.lineTo(b.x,b.y);cx.stroke()});cx.setLineDash([]);
if(!reduz&&ts-ultimo>900){ultimo=ts;novo();if(pulsos.length>14)pulsos.shift()}
pulsos=pulsos.filter(p=>{p.t+=.018;if(p.t<0)return true;
if(p.t>=1){if(p.fila.length){p.e=p.fila.shift();p.t=0;return true}return false}
const[a,b]=seg(p.e),x=a.x+(b.x-a.x)*p.t,y=a.y+(b.y-a.y)*p.t;
const g=cx.createRadialGradient(x,y,0,x,y,10);g.addColorStop(0,'#00e5b8');g.addColorStop(1,'rgba(0,229,184,0)');cx.fillStyle=g;cx.beginPath();cx.arc(x,y,10,0,7);cx.fill();return true});
N.forEach(n=>{cx.fillStyle=n.plan?'#14111f':'#0f121b';cx.strokeStyle=n.plan?'#9d6bff':'#00e5b8';cx.setLineDash(n.plan?[3,3]:[]);cx.beginPath();cx.arc(n.x,n.y,n.n.length>8?4+0:6,0,7);cx.fill();cx.stroke();cx.setLineDash([]);
cx.fillStyle='#cfd6ea';cx.font='10px monospace';cx.textAlign='center';cx.fillText(n.n,n.x,n.y-11)});
requestAnimationFrame(desenha)}
document.addEventListener('visibilitychange',()=>{visivel=!document.hidden});
if(reduz){pulsos=[];desenha(0)}else requestAnimationFrame(desenha);
})();
fetch('/api/prompts').then(r=>r.json()).then(b=>{
const E=x=>String(x).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
document.getElementById('aviso').textContent=b.aviso;
document.getElementById('mot').innerHTML=b.motores.map(m=>'<div class="c"><canvas class="m3d" data-m="'+E(m.id)+'" aria-hidden="true"></canvas><b>'+E(m.nome)+'</b><small>Economia: '+E(m.economia)+'<br>Risco: <span class="'+(m.risco.startsWith('ALTO')?'no':'ok')+'">'+E(m.risco)+'</span><br>'+E(m.perda)+'<br>'+E(m.quando)+'</small><br><a class="b s" href="/painel?modo='+m.id+'">Usar este motor</a></div>').join('');
if(window.m3dIniciar)window.m3dIniciar();
window._mods=b.modelos;
document.getElementById('mod').innerHTML=b.modelos.map((m,i)=>'<div class="c"><span class="tag">MODELO PRONTO · '+E(m.tipo)+'</span><b>'+E(m.id)+'</b><small>'+m.frases+' frases · '+m.tokens_cheio+' → '+m.tokens_curto+' tokens ('+m.economia+' menos, sem perda)</small><pre style="white-space:pre-wrap;font:12px/1.5 monospace;color:#aab;max-height:120px;overflow:auto;margin-top:6px">'+E(m.prompt)+'</pre>'+
'<button class="b s" data-i="'+i+'" data-k="p">Copiar prompt completo</button><button class="b s" data-i="'+i+'" data-k="r">Copiar '+E(m.referencia)+'</button><a class="b" href="/painel?texto='+encodeURIComponent(m.referencia)+'">Usar no painel</a></div>').join('');
document.querySelectorAll('button[data-k]').forEach(bt=>bt.onclick=()=>{const m=window._mods[bt.dataset.i];
navigator.clipboard.writeText(bt.dataset.k==='p'?m.prompt:m.referencia).then(()=>{bt.textContent='Copiado!'}).catch(()=>{bt.textContent='Selecione e copie manualmente'})});
}).catch(()=>{});
fetch('/health').then(r=>r.json()).then(d=>{const a=d.ativos||{};
document.getElementById('vivo').innerHTML='Versão <b>'+d.versao+'</b><br>IAs de texto ativas: <b>'+((a.texto||[]).join(', ')||'nenhuma configurada')+
'</b><br>Geradores de vídeo: <b>'+((a.video||[]).join(', ')||'nenhum')+'</b>'}).catch(()=>{document.getElementById('vivo').textContent='Servidor indisponível agora.'});
</script><script>
(function(){
const TAU=Math.PI*2,OURO='#ffc72b',PRATA='#c4c8d4',COBRE='#c27a3a',NEON='#00e5b8',VERM='#ff5a6e',AZUL='#6fa8ff';
function sh(hex,f,w){const n=parseInt(hex.slice(1),16);let r=n>>16,g=n>>8&255,b=n&255;
r=Math.min(255,r*f+(255-r*f)*(w||0));g=Math.min(255,g*f+(255-g*f)*(w||0));b=Math.min(255,b*f+(255-b*f)*(w||0));return 'rgb('+(r|0)+','+(g|0)+','+(b|0)+')'}
// engrenagem maciça: camadas de espessura + face + bisel + cubo + raios (dentes trapezoidais)
function gear(c,x,y,R,N,ang,col,dep){dep=dep==null?4:dep;const p=TAU/N,a=R*.13,d=R*.16;
for(let k=dep;k>=0;k--){c.beginPath();const ox=x+k*.8,oy=y-k;
for(let i=0;i<N;i++){for(const[da,rr]of[[-.27,R-d],[-.14,R+a],[.14,R+a],[.27,R-d]]){const t=ang+i*p+da*p;
c[(i||da>-.3)?'lineTo':'moveTo'](ox+Math.cos(t)*rr,oy+Math.sin(t)*rr*.9)}}
c.closePath();c.fillStyle=sh(col,k?.3+.12*(dep-k):.9);c.fill();if(!k){c.strokeStyle=sh(col,1,.35);c.lineWidth=.8;c.stroke()}}
c.beginPath();c.ellipse(x,y,R*.78,R*.78*.9,0,0,TAU);c.fillStyle=sh(col,1,.15);c.fill();
c.strokeStyle=sh(col,.22);c.lineWidth=Math.max(1.5,R*.07);c.beginPath();
for(let j=0;j<5;j++){const t=ang+j*TAU/5;c.moveTo(x+Math.cos(t)*R*.38,y+Math.sin(t)*R*.38*.9);c.lineTo(x+Math.cos(t)*R*.78,y+Math.sin(t)*R*.78*.9)}c.stroke();
c.beginPath();c.ellipse(x,y,R*.36,R*.36*.9,0,0,TAU);c.fillStyle=sh(col,.3);c.fill();
c.beginPath();c.ellipse(x,y,R*.12,R*.12*.9,0,0,TAU);c.fillStyle=sh(col,1.1,.3);c.fill()}
// filha encaixada no pai (phi = direção pai->filha)
function fase(ap,Np,Nf,phi){return phi+Math.PI+(Math.PI-Np*(ap-phi))/Nf}
function cubo(c,x,y,s,ax,ay,col,txt){const V=[];for(const X of[-1,1])for(const Y of[-1,1])for(const Z of[-1,1]){
let x1=X,y1=Y*Math.cos(ax)-Z*Math.sin(ax),z1=Y*Math.sin(ax)+Z*Math.cos(ax);
const x2=x1*Math.cos(ay)+z1*Math.sin(ay),z2=-x1*Math.sin(ay)+z1*Math.cos(ay);V.push([x2,y1,z2])}
const F=[[0,1,3,2],[4,6,7,5],[0,4,5,1],[2,3,7,6],[0,2,6,4],[1,5,7,3]];
const fs=F.map(f=>({f,z:f.reduce((s,i)=>s+V[i][2],0)/4,n:(()=>{const a=V[f[0]],b=V[f[1]],d=V[f[3]];return((b[0]-a[0])*(d[1]-a[1])-(b[1]-a[1])*(d[0]-a[0]))})()})).sort((p,q)=>q.z-p.z);
for(const q of fs){c.beginPath();q.f.forEach((i,k)=>c[k?'lineTo':'moveTo'](x+V[i][0]*s,y+V[i][1]*s));c.closePath();
c.fillStyle=sh(col,.35+.5*Math.abs(q.n)/4);c.fill();c.strokeStyle=sh(col,1,.4);c.lineWidth=1;c.stroke();
if(txt&&q.z<0&&Math.abs(q.n)>2.2){const cx=q.f.reduce((s,i)=>s+V[i][0],0)/4,cy=q.f.reduce((s,i)=>s+V[i][1],0)/4;c.fillStyle='rgba(255,255,255,.85)';c.font='bold '+(s*.55|0)+'px monospace';c.textAlign='center';c.fillText(txt,x+cx*s,y+cy*s+s*.2)}}}
function cil(c,x,y,r,l,ang,col){const g=c.createLinearGradient(x,y-r,x,y+r);g.addColorStop(0,sh(col,1.1,.3));g.addColorStop(.5,sh(col,.7));g.addColorStop(1,sh(col,.3));
c.fillStyle=g;c.fillRect(x-l/2,y-r,l,2*r);c.strokeStyle=sh(col,.25);c.lineWidth=1.2;for(let i=0;i<6;i++){const yy=y-r+(((ang*r*1.2+i*r*.7)%(2*r)+2*r)%(2*r));c.beginPath();c.moveTo(x-l/2,yy);c.lineTo(x+l/2,yy);c.stroke()}}
const T={
auto(c,w,h,t){const a1=t*.9,R1=h*.30,R2=h*.20,x1=w*.36,x2=x1+R1+R2;gear(c,x1,h*.5,R1,14,a1,OURO);gear(c,x2,h*.5,R2,10,fase(a1,14,10,0),PRATA);
const k=.5+.5*Math.sin(t*2);c.strokeStyle='rgba(0,229,184,'+(.4+.5*k)+')';c.lineWidth=3;c.beginPath();c.arc(w*.82,h*.5,h*.26,0,TAU);c.stroke();
c.beginPath();c.moveTo(w*.82-h*.11,h*.5);c.lineTo(w*.82-h*.02,h*.5+h*.1);c.lineTo(w*.82+h*.13,h*.5-h*.1);c.stroke()},
agressivo(c,w,h,t){gear(c,w*.24,h*.5,h*.36,16,t*2.2,OURO);const ph=(t*.6)%1,len=1-.65*Math.min(1,ph*1.4);
for(let i=0;i<5;i++){const y=h*.2+i*h*.15,L=w*.42*(i%2?len:Math.max(.35,len+.1));c.fillStyle=sh(i%2?COBRE:PRATA,.85);c.fillRect(w*.5,y,L,h*.08);c.fillStyle='rgba(0,0,0,.35)';c.fillRect(w*.5,y+h*.08,L,2)}
c.strokeStyle=VERM;c.lineWidth=2;const bx=w*.5+w*.42*len+4;c.beginPath();c.moveTo(bx,h*.12);c.lineTo(bx,h*.88);c.stroke()},
light(c,w,h,t){gear(c,w*.5,h*.52,h*.3,12,t*.6,PRATA,3);for(let i=0;i<5;i++){const u=(t*.25+i*.2)%1;c.fillStyle='rgba(0,229,184,'+(1-u)*.6+')';c.beginPath();c.arc(w*(.2+i*.15),h*(.85-u*.7),2.5,0,TAU);c.fill()}},
v24(c,w,h,t){const f=Math.sin(t*7)>.92;cubo(c,w*.5,h*.5,h*.3,t*.8,t*1.1,f?'#ff9aa8':VERM,'§ID');c.fillStyle='rgba(255,90,110,.8)';c.font='11px monospace';c.textAlign='left';c.fillText('códigos: risco alto',8,h-8)},
maestro(c,w,h,t){const cx=w*.5,cy=h*.5,Rs=h*.17,Rp=h*.11,Rr=Rs+Rp*2+1;c.strokeStyle=sh(OURO,.5);c.lineWidth=3;c.beginPath();c.ellipse(cx,cy,Rr+h*.06,(Rr+h*.06)*.9,0,0,TAU);c.stroke();
const as=t*.8;gear(c,cx,cy,Rs,10,as,OURO,3);for(let i=0;i<3;i++){const phi=t*.25+i*TAU/3,px=cx+Math.cos(phi)*(Rs+Rp),py=cy+Math.sin(phi)*(Rs+Rp)*.9;gear(c,px,py,Rp,8,fase(as,10,8,phi),PRATA,3)}},
arena(c,w,h,t){const cx=w*.5,cy=h*.52,R=h*.2,r=h*.13,a=t*.7;for(let i=0;i<3;i++){const phi=-Math.PI/2+(i-1)*1.25,px=cx+Math.cos(phi)*(R+r),py=cy+Math.sin(phi)*(R+r)*.9;gear(c,px,py,r,9,fase(a,12,9,phi),[PRATA,COBRE,PRATA][i],3)}gear(c,cx,cy,R,12,a,OURO,4)},
compressao(c,w,h,t){const ph=t*2;cil(c,w*.5,h*.28,h*.12,w*.18,ph,PRATA);cil(c,w*.5,h*.72,h*.12,w*.18,-ph,PRATA);
for(let i=0;i<7;i++){const x=((t*40+i*w*.14)%(w*.98));const u=Math.min(1,Math.max(0,(x-w*.38)/(w*.24)));const th=h*.28*(1-.7*u);c.fillStyle=sh(u>.5?NEON:COBRE,.9);c.fillRect(x,h*.5-th/2,w*.1,th)}},
arvore(c,w,h,t){const N=[[.5,.12],[.28,.38],[.72,.38],[.16,.7],[.4,.72],[.6,.72],[.84,.7],[.5,.92]],E=[[0,1],[0,2],[1,3],[1,4],[2,5],[2,6],[4,7],[5,7]];
c.lineWidth=2;E.forEach(e=>{c.strokeStyle=sh(COBRE,.6);c.beginPath();c.moveTo(N[e[0]][0]*w,N[e[0]][1]*h);c.lineTo(N[e[1]][0]*w,N[e[1]][1]*h);c.stroke()});
for(let k=0;k<4;k++){const u=(t*.5+k*.25)%1,e=E[(k*2+((t*.5+k*.25)|0))%6],x=(N[e[0]][0]+(N[e[1]][0]-N[e[0]][0])*u)*w,y=(N[e[0]][1]+(N[e[1]][1]-N[e[0]][1])*u)*h;const g=c.createRadialGradient(x,y,0,x,y,10);g.addColorStop(0,NEON);g.addColorStop(1,'rgba(0,229,184,0)');c.fillStyle=g;c.beginPath();c.arc(x,y,10,0,TAU);c.fill()}
N.forEach((n,i)=>{const g=c.createRadialGradient(n[0]*w-2,n[1]*h-2,1,n[0]*w,n[1]*h,9);g.addColorStop(0,sh(i%2?PRATA:OURO,1,.4));g.addColorStop(1,sh(i%2?PRATA:OURO,.4));c.fillStyle=g;c.beginPath();c.arc(n[0]*w,n[1]*h,7,0,TAU);c.fill()})},
comandos(c,w,h,t){cubo(c,w*.5,h*.5,h*.3,t*.7,t*.9,'#3fd6b0','/')},
video(c,w,h,t){const cx=w*.38,cy=h*.5,R=h*.34;c.fillStyle=sh(PRATA,.4);c.beginPath();c.ellipse(cx,cy,R,R*.9,0,0,TAU);c.fill();c.fillStyle=sh(PRATA,.8);c.beginPath();c.ellipse(cx,cy-2,R,R*.9,0,0,TAU);c.fill();
for(let i=0;i<6;i++){const a=t*1.2+i*TAU/6;c.fillStyle='#04050a';c.beginPath();c.ellipse(cx+Math.cos(a)*R*.55,cy-2+Math.sin(a)*R*.55*.9,R*.16,R*.14,0,0,TAU);c.fill()}
for(let i=0;i<6;i++){const x=w*.62+((i*w*.1-t*30)%(w*.4)+w*.4)%(w*.4)-w*.05;c.fillStyle=sh(COBRE,.7);c.fillRect(x,h*.34,w*.08,h*.32);c.fillStyle='#04050a';c.fillRect(x+w*.02,h*.4,w*.04,h*.2)}},
app(c,w,h,t){const pw=h*.5,x=w*.5-pw/2,y=h*.06,ph=h*.88;c.fillStyle='#0a0d16';c.strokeStyle=sh(PRATA,.9);c.lineWidth=2.5;c.beginPath();c.roundRect?c.roundRect(x,y,pw,ph,9):c.rect(x,y,pw,ph);c.fill();c.stroke();
gear(c,w*.5,h*.38,pw*.26,10,t,OURO,3);gear(c,w*.5+pw*.2,h*.66,pw*.14,7,fase(t,10,7,Math.atan2(h*.28,pw*.2)),PRATA,2);
const k=.5+.5*Math.sin(t*3);c.fillStyle='rgba(0,229,184,'+(.3+.5*k)+')';c.fillRect(x+pw*.2,y+ph*.9,pw*.6,3)},
cofre(c,w,h,t){const cx=w*.5,cy=h*.5;[[.42,OURO,1],[.31,PRATA,-1.6],[.2,COBRE,2.4]].forEach(([r,col,v])=>{const R=h*r;c.fillStyle=sh(col,.45);c.beginPath();c.ellipse(cx,cy+3,R,R*.9,0,0,TAU);c.fill();c.fillStyle=sh(col,.85);c.beginPath();c.ellipse(cx,cy,R,R*.9,0,0,TAU);c.fill();
c.strokeStyle='rgba(0,0,0,.55)';c.lineWidth=1.3;for(let i=0;i<24;i++){const a=t*v*.5+i*TAU/24;c.beginPath();c.moveTo(cx+Math.cos(a)*R*.86,cy+Math.sin(a)*R*.86*.9);c.lineTo(cx+Math.cos(a)*R,cy+Math.sin(a)*R*.9);c.stroke()}});
c.fillStyle='#04050a';c.beginPath();c.ellipse(cx,cy,h*.09,h*.08,0,0,TAU);c.fill();c.fillStyle=OURO;c.fillRect(cx-2,cy-h*.5,4,h*.07)}
};
const vis=new Set(),io='IntersectionObserver'in window?new IntersectionObserver(es=>es.forEach(e=>e.isIntersecting?vis.add(e.target):vis.delete(e.target)),{rootMargin:'120px'}):null;
const reduz=matchMedia('(prefers-reduced-motion: reduce)').matches;let ult=0;
window.m3dIniciar=function(){document.querySelectorAll('canvas.m3d:not([data-ok])').forEach(cv=>{cv.dataset.ok=1;const dpr=Math.min(2,devicePixelRatio||1),W=cv.clientWidth||300,H=cv.clientHeight||120;cv.width=W*dpr;cv.height=H*dpr;cv._c=cv.getContext('2d');cv._c.scale(dpr,dpr);cv._w=W;cv._h=H;
if(io)io.observe(cv);else vis.add(cv);if(reduz)pinta(cv,2.4)})};
function pinta(cv,t){const c=cv._c,fn=T[cv.dataset.m]||T.maestro;c.clearRect(0,0,cv._w,cv._h);try{fn(c,cv._w,cv._h,t)}catch(e){}}
function loop(ts){if(!document.hidden&&!reduz&&ts-ult>33){ult=ts;vis.forEach(cv=>pinta(cv,ts/1000))}requestAnimationFrame(loop)}
window.m3dIniciar();requestAnimationFrame(loop);
})();
</script></body></html>"""
