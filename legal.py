"""Página de privacidade do Ttoken Studio (modelo honesto, descreve o que o código faz hoje).
Não substitui revisão de um advogado antes de vender."""

PRIVACIDADE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Privacidade - Ttoken Studio</title>
<style>body{background:#0b0b12;color:#e8e8f0;font:16px/1.6 system-ui,sans-serif;margin:0}
main{max-width:720px;margin:0 auto;padding:24px 16px 60px}h1{font-size:1.6rem}
h2{font-size:1.1rem;color:#00d2a0;margin-top:1.6em}a{color:#00d2a0}
li{margin:.3em 0}.aviso{background:#1a1a26;border-left:3px solid #6c5ce7;padding:10px 14px;border-radius:6px}</style>
</head><body><main>
<h1>Privacidade e uso das suas chaves</h1>
<p class="aviso">Texto explicativo sobre como o app funciona hoje. Pode mudar quando o app mudar.</p>
<h2>O que enviamos</h2>
<ul>
<li>A ideia que você digita, o idioma e a plataforma vão para o nosso servidor e de lá para
as IAs de texto e de vídeo configuradas (por exemplo Groq, Gemini, Magic Hour, BigModel, Agnes).
Cada uma delas tem a própria política de privacidade.</li>
<li>Se você escolher uma foto de produto, ela fica disponível por um link aleatório por cerca de
1 hora e depois é apagada da memória do servidor.</li>
</ul>
<h2>O que guardamos</h2>
<ul>
<li>Pedidos em andamento e vídeos ficam na memória do servidor por cerca de 1 hora.</li>
<li>Respostas de texto ficam em cache na memória por até 6 horas para economizar IA.</li>
<li>Cofre de moedas: guardamos um código aleatório da sua conta (criado no seu aparelho, sem
e-mail e sem senha) e o saldo de moedas.</li>
<li>O limite por hora usa o seu endereço de IP, só na memória.</li>
</ul>
<h2>Suas chaves (BYOK)</h2>
<ul>
<li>Chaves de IA coladas no app ficam salvas no seu celular. No modo "usar sem servidor"
elas são enviadas só ao provedor da própria chave.</li>
<li>Não compartilhe o app, prints ou arquivos que contenham as suas chaves.</li>
</ul>
<h2>O que não fazemos</h2>
<ul><li>Não vendemos seus dados e não pedimos e-mail para usar o app.</li></ul>
<h2>Limites e avisos</h2>
<ul>
<li>As IAs podem errar. Revise o conteúdo antes de publicar.</li>
<li>O servidor gratuito pode reiniciar e apagar pedidos em andamento e saldos do cofre
quando não houver armazenamento permanente configurado.</li>
</ul>
</main></body></html>"""
