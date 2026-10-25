"""Benchmark: Ttokem (nosso) x T24 unificado x NHHC-4F, nos MESMOS 50 prompts.
Roda local ou no Colab: python benchmark_concorrentes.py (usa tiktoken se existir).
Mede economia em tokens, fatos/lógica/verbos intactos (auditor do Ttokem aplicado a TODOS),
palavras de conteudo perdidas e palavras INVENTADAS (que nao estavam no original)."""
import re, sys, os, time, statistics, hashlib, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
try:
    import tiktoken
    _e = tiktoken.get_encoding("cl100k_base")
    def tok(s): return len(_e.encode(s))
    TOKENIZER = "tiktoken cl100k_base"
except Exception:
    def tok(s): return max(1, round(len(re.findall(r"\w+|[^\w\s]", s)) * 1.3))
    TOKENIZER = "aproximado (sem tiktoken)"
import ttokem, comandos

# ---------- concorrente 1: T24 unificado (codigo do usuario, sem cache) ----------
ECONOMIA_MINIMA_PCT = 10.0   # piso (era 25% → causava 34 fallbacks)
ECONOMIA_ALVO_PCT   = 30.0   # alvo (se T24 atinge, não tenta mais nada)
CACHE_SEMANTICO_THRESHOLD = 0.92
USAR_LLMLINGUA = False       # rejeitado nos testes

# Guardrails (ordem: compostos primeiro, para não quebrar)
GUARDRAIL_TERMS = [
    "arm64-v8a", "postgresql", "buildozer", "openrouter",
    "fastapi", "pydantic", "9:16", "kivy", "docker", "render",
    "jwt", "apk", "4k", "8k",
]

# ============================================================
# 2. FIX DO BUG: PROTECT_RE único (guardrails + números num passe)
# ============================================================
PROTECT_RE = re.compile(
    r'\b(?:' + '|'.join(re.escape(t) for t in GUARDRAIL_TERMS) + r')\b'
    r'|\d+(?:[.:,]\d+)*%?',
    re.I
)

STOPWORDS = set("""
and the with so that into while but or for a an to of in on as by is are it this
be at from you your can please make create want i do have has will would could should
e o a os as de da do em um uma para com não no se na por mais dos como mas ou quando
muito nos já eu também pelo pela favor quero fazer criar gerar ter preciso voce você
poderia seu sua meu minha nosso nossa ele ela eles elas
""".split())
QUALIFICADORES = {"super", "muito", "bem", "alta", "total", "completo"}

# ============================================================
# 3. T24 CASCATA (base)
# ============================================================
def cascata_t24(texto, tipo="geral"):
    if not texto: return ""
    t = texto.lower()
    for w in ["por favor","você poderia","poderia","quero","gostaria que",
              "please","could you","make sure"]:
        t = t.replace(w, "")
    if tipo == "video":
        t = t.replace("cinematográfico","cinematic 8k").replace("carro futurista","futuristic cyberpunk car")
    elif tipo == "codigo_apk":
        t = t.replace("script em python","python script").replace("aplicativo mobile","mobile app")
    t = re.sub(r'[\n\r\t]+', ' ', t)
    t = re.sub(r'[,\.\?!;—\-"]+', ' ', t)
    palavras = [re.sub(r'^[,\.\?!;—\-"]+|[,\.\?!;—\-"]+$','',p) for p in t.split()]
    filtradas = [p for p in palavras if p not in STOPWORDS and len(p) > 2]
    filtradas = [w for w in filtradas if w not in QUALIFICADORES]
    return re.sub(r'\s+', ' ', " ".join(filtradas)).strip()

# ============================================================
# 4. T24 SEGURO (bug do NUM_RE corrigido)
# ============================================================
def cascata_t24_segura(texto, tipo="geral"):
    mapa = {}
    def _make(m):
        # Placeholder SEM dígitos (evita corrupção pelo NUM_RE)
        key = f"QX{chr(65 + len(mapa) % 26)}{chr(97 + len(mapa) % 26)}Q"
        mapa[key] = m.group(0)
        return key
    protegido = PROTECT_RE.sub(_make, texto)
    comprimido = cascata_t24(protegido, tipo)
    for key, val in mapa.items():
        comprimido = comprimido.replace(key.lower(), val)
    return re.sub(r'\s+', ' ', comprimido).strip()

# ---------- concorrente 2: NHHC-4F (codigo do usuario) ----------

class NeuralHereditaryNode:
    """Nó da Rua Neural com propriedades hereditárias, pesos sinápticos e Fatores de Comunicação da IA."""
    def __init__(self, name, factor_type, pattern, compressed_token, parent=None, guardrails=None, synaptic_weights=None):
        self.name = name
        self.factor_type = factor_type  # 'ROLE', 'CONTEXT', 'TASK', 'CONSTRAINTS', 'ROOT'
        self.pattern = pattern
        self.compressed_token = compressed_token
        self.parent = parent
        self.guardrails = guardrails or []
        self.synaptic_weights = synaptic_weights or {}
        self.children = []
        
        if parent:
            parent.children.append(self)
            self.guardrails = list(set(self.guardrails + parent.guardrails))

class TtokemNeuralEngineBatch:
    """Coordenador Neural-Hereditário da Rua Neural (NHHC-4F) para testes em lote."""
    def __init__(self):
        self.root = NeuralHereditaryNode(
            name="ROOT_GOVERNOR", factor_type="ROOT", pattern="", compressed_token="", 
            guardrails=["9:16", "50%", "100%", "fastapi", "kivy", "buildozer", "docker", "render"]
        )
        self.nodes = {}
        self._build_neural_street()

    def _build_neural_street(self):
        # Mapeamento dos Blocos Hereditários na Rua Neural
        role_node = NeuralHereditaryNode("ROLE_DEV", "ROLE", "expert python developer", "role:dev", parent=self.root)
        context_node = NeuralHereditaryNode("CONTEXT_PERF", "CONTEXT", "high-performance system", "ctx:perf", parent=self.root)
        backend = NeuralHereditaryNode("TASK_BACKEND", "TASK", "python script using fastapi", "py:fastapi", parent=self.root)
        mobile = NeuralHereditaryNode("TASK_MOBILE", "TASK", "android apk via buildozer", "apk:buildozer", parent=backend)
        constraints_node = NeuralHereditaryNode("CONST_UI", "CONSTRAINTS", "cyberpunk obsidian ui", "cst:ui_cyber", parent=self.root)
        video_node = NeuralHereditaryNode("MEDIA_VIDEO", "TASK", "cinematic 8k futuristic cyberpunk car", "vid:cyber_8k", parent=self.root)
        infra_node = NeuralHereditaryNode("INFRA_DOCKER", "CONTEXT", "docker render postgresql", "infra:docker_pg", parent=self.root)

        self.nodes = {
            "role": role_node, "context": context_node, 
            "backend": backend, "mobile": mobile, "constraints": constraints_node,
            "video": video_node, "infra": infra_node
        }

    def processar(self, texto_usuario: str) -> dict:
        t0 = time.perf_counter()
        txt = texto_usuario.lower()
        cluster_ativado = []
        
        for name, node in self.nodes.items():
            if node.pattern and node.pattern in txt:
                if node not in cluster_ativado:
                    cluster_ativado.append(node)
                ancestral = node.parent
                while ancestral and ancestral != self.root:
                    if ancestral not in cluster_ativado:
                        cluster_ativado.insert(0, ancestral)
                    ancestral = ancestral.parent

        for node in cluster_ativado:
            if node.pattern:
                txt = txt.replace(node.pattern, node.compressed_token)

        stopwords = {"and", "the", "with", "so", "that", "into", "while", "but", "or", "for", "a", "an", "to", "of", "in", "on", "as", "by", "e", "o", "a", "de", "da", "do", "em", "para", "com"}
        palavras = [p for p in txt.split() if p not in stopwords or p in self.root.guardrails]
        
        prompt_comprimido = " ".join(palavras)
        prompt_comprimido = re.sub(r'\s+', ' ', prompt_comprimido).strip()
        t1 = time.perf_counter()

        orig_len = len(texto_usuario)
        comp_len = len(prompt_comprimido)
        economia = ((orig_len - comp_len) / orig_len) * 100 if orig_len > 0 else 0

        return {
            "original": texto_usuario,
            "comprimido": prompt_comprimido,
            "orig_len": orig_len,
            "comp_len": comp_len,
            "economia": round(economia, 2),
            "latencia_ms": round((t1 - t0) * 1000, 4)
        }

# ---------- 50 prompts ----------
PROMPTS = [
    ("video", "Por favor, você poderia gerar um vídeo cinematográfico em formato 9:16 de um carro futurista correndo na chuva à noite com luzes neon?"),
    ("codigo_apk", "Create a high-performance Python script using FastAPI and Kivy that compiles into an Android APK via Buildozer, featuring a cyberpunk obsidian UI."),
    ("codigo_apk", "Preciso de um aplicativo mobile completo com autenticação de usuário por email e modelo freemium com 50% de capacidade grátis."),
    ("video", "Quero um vídeo sobre economia de tokens com alta qualidade e estilo futurista."),
    ("geral", "Por favor, você poderia criar um script em Python super rápido usando FastAPI que faça o download automático de vídeos verticais?"),
    ("codigo_apk", "Desenvolva um backend robusto em FastAPI rodando no Docker no Render com banco de dados PostgreSQL."),
    ("video", "Crie um vídeo vertical 9:16 de uma paisagem montanhosa ao pôr do sol com efeitos cinematográficos."),
    ("geral", "Como posso configurar o Buildozer para compilar pacotes APK arm64-v8a sem erros de dependência?"),
    ("codigo_apk", "Faça uma tela de login moderna em Kivy com validação de email usando pydantic e email-validator."),
    ("video", "Gere um vídeo em alta definição com animações fluidas de inteligência artificial em estilo cyberpunk."),
    ("geral", "Qual a melhor forma de estruturar rotas assíncronas em um projeto FastAPI voltado para microserviços?"),
    ("codigo_apk", "Escreva um script em Python que leia arquivos JSON e valide campos obrigatórios com Pydantic v2."),
    ("video", "Preciso de um prompt otimizado para gerar um avatar realista em 9:16 falando sobre inteligência artificial."),
    ("geral", "Como fazer o deploy de uma aplicação Python no Render utilizando um Dockerfile personalizado?"),
    ("codigo_apk", "Crie um botão interativo em Kivy que mude de cor ao ser pressionado e dispare uma requisição HTTP."),
    ("video", "Quero um cenário espacial futurista com naves voando em órbita de um planeta anelado em 4K."),
    ("geral", "Quais são as principais vantagens de usar o OpenRouter para gerenciar múltiplos modelos de linguagem em uma API?"),
    ("codigo_apk", "Configure o arquivo buildozer.spec para incluir permissões de internet e estado de rede no Android."),
    ("video", "Gere uma animação em looping de ondas de neon azul e roxo em formato vertical 9:16."),
    ("geral", "Como implementar um sistema de controle de acesso baseado em planos gratuito e pro em FastAPI?"),
    ("codigo_apk", "Crie uma API REST completa para cadastro de clientes com rotas GET, POST, PUT e DELETE."),
    ("video", "Crie um vídeo cinematográfico de uma cidade cyberpunk à noite com chuva intensa e reflexos no asfalto."),
    ("geral", "Explique como funciona a compressão determinística de prompts em comparação com modelos preditivos."),
    ("codigo_apk", "Escreva um código Kivy para exibir uma lista rolável de itens carregados dinamicamente de uma API."),
    ("video", "Gere uma cena realista de um robô inteligente escrevendo códigos em um terminal holográfico."),
    ("geral", "Como tratar exceções globais em FastAPI para retornar mensagens de erro padronizadas em JSON?"),
    ("codigo_apk", "Adicione suporte a variáveis de ambiente seguras usando python-dotenv no backend."),
    ("video", "Quero um vídeo abstrato de partículas de luz dourada flutuando em fundo escuro."),
    ("geral", "Quais bibliotecas Python são essenciais para compilar aplicações nativas para Android?"),
    ("codigo_apk", "Crie uma função assíncrona em Python para consultar status de tarefas em segundo plano."),
    ("video", "Gere um vídeo em formato 9:16 mostrando um timelapse de nuvens correndo velozes sobre montanhas."),
    ("geral", "Como otimizar o tempo de resposta de endpoints FastAPI em servidores de produção?"),
    ("codigo_apk", "Implemente um mecanismo de cache em memória para evitar consultas repetitivas ao banco de dados."),
    ("video", "Crie uma cena de um laboratório de alta tecnologia com cientistas analisando dados genéticos em telas 3D."),
    ("geral", "Qual a importância de blindar parâmetros numéricos e versões em compressores de texto por IA?"),
    ("codigo_apk", "Configure rotas estáticas no FastAPI para servir arquivos binários como instaladores APK."),
    ("video", "Gere um vídeo vertical de um carro esportivo acelerando em uma rodovia deserta ao amanhecer."),
    ("geral", "Como realizar testes automatizados em rotas FastAPI utilizando pytest e TestClient?"),
    ("codigo_apk", "Crie um layout responsivo em Kivy adaptável para diferentes resoluções de telas mobile."),
    ("video", "Quero um visual futurista com portais dimensionais abrindo em um ambiente escuro."),
    ("geral", "Como configurar políticas de CORS em aplicações FastAPI para aceitar requisições de frontend Next.js?"),
    ("codigo_apk", "Escreva um script para automatizar o empacotamento de APKs via Docker e Buildozer."),
    ("video", "Gere uma animação 9:16 de um globo terrestre holográfico girando com conexões de rede brilhantes."),
    ("geral", "Quais são as melhores práticas de segurança para autenticação baseada em tokens JWT?"),
    ("codigo_apk", "Crie uma classe de modelo de dados com Pydantic para validar pagamentos internacionais."),
    ("video", "Crie uma cena cinematográfica de uma tempestade em alto mar com ondas gigantescas."),
    ("geral", "Como monitorar o uso de recursos de CPU e memória em containers Docker no Render?"),
    ("codigo_apk", "Implemente uma interface gráfica limpa em estilo obsidian dark mode para o aplicativo mobile."),
    ("video", "Gere um vídeo em 9:16 de foguetes de alta potência decolando em direção ao espaço sideral."),
    ("geral", "Como estruturar um projeto SaaS completo unindo backend FastAPI, motor T24 e publicação automatizada?"),
]

_STOP = set("a o as os um uma de da do das dos em no na nos nas por para com e ou que se the of to in on and with for an as by is are it this".split())
def conteudo(t): return {w for w in re.findall(r"[\wáàâãéêíóôõúç'-]+", t.lower()) if len(w) > 3 and w not in _STOP}

def _ours(p, modo):
    c = comandos.analisar(p)
    o = ttokem.compress(c["texto"], modo, c["tipo"], presets=True)
    t = o["text"]
    return ttokem.expandir_presets(t) if "@" in t else t

def rodar():
    nhhc = TtokemNeuralEngineBatch()
    motores = {
        "T24 unificado": lambda p, t: cascata_t24_segura(p, t),
        "NHHC-4F": lambda p, t: nhhc.processar(p)["comprimido"],
        "Ttokem seguro": lambda p, t: _ours(p, "auto"),
        "Ttokem agressivo": lambda p, t: _ours(p, "agressivo"),
    }
    res = {}
    for nome, fn in motores.items():
        eco, intactas, perdidas, inventadas, ms = [], 0, [], [], []
        for tipo, p in PROMPTS:
            t0 = time.perf_counter(); o = fn(p, tipo); ms.append((time.perf_counter() - t0) * 1000)
            to, tc = tok(p), tok(o)
            eco.append((to - tc) / to * 100 if tc <= to else 0.0)
            intactas += bool(ttokem.semantica(p, o)["intacta"])
            co, cc = conteudo(p), conteudo(o)
            perdidas.append(len(co - cc) / len(co) * 100 if co else 0)
            inventadas.append(len(cc - co))
        res[nome] = {"economia_%": round(statistics.mean(eco), 1),
                     "intactos": f"{intactas}/{len(PROMPTS)}",
                     "conteudo_perdido_%": round(statistics.mean(perdidas), 1),
                     "palavras_inventadas": sum(inventadas),
                     "latencia_ms": round(statistics.mean(ms), 2)}
    return res

if __name__ == "__main__":
    print("Tokenizador:", TOKENIZER)
    r = rodar()
    cols = list(next(iter(r.values())).keys())
    print(f"{'motor':<18}" + "".join(f"{c:>22}" for c in cols))
    for n, v in r.items():
        print(f"{n:<18}" + "".join(f"{str(v[c]):>22}" for c in cols))
