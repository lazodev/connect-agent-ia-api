#!/usr/bin/env python3
from __future__ import annotations

"""
Jarvis Code Assistant - Terminal Interativo (Multi-Mode: ask | plan | agent)
"""

import anthropic
import os
import sys
import urllib.request
import urllib.error
from dotenv import load_dotenv
from pathlib import Path
import difflib
import re
import json

if sys.stdout.encoding != "utf-8":
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)
if sys.stdin.encoding != "utf-8":
    sys.stdin  = open(sys.stdin.fileno(),  mode="r", encoding="utf-8", buffering=1)

# ─── Setup ───────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

API_KEY = os.getenv("API_KEY")
MODEL = os.getenv("MODEL", "claude-3-haiku-20251001")
PROVIDER = os.getenv("PROVIDER", "anthropic").lower()

OLLAMA_MODEL = os.getenv("OLLAMA_MODEL",  "qwen2.5:latest")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_FALLBACK = os.getenv("OLLAMA_FALLBACK", "true").lower() == "true"

if PROVIDER == "anthropic" and not API_KEY:
    raise ValueError("X API_KEY não encontrada no .env (necessária para PROVIDER=anthropic)")

_anthropic_client = anthropic.Anthropic(api_key=API_KEY or "sk-dummy")

# ─── Cores ───────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

# ─── Ollama helpers ──────────────────────────────────────────────
provider_atual = PROVIDER
modelo_atual = MODEL
ollama_model_atual = OLLAMA_MODEL

# ─── Estado ──────────────────────────────────────────────────────
historico   = []
arquivos_carregados = {}
modo_atual  = "ask"   # ask | plan | agent
plano_ativo = ""      # resumo do que foi planejado no modo ask/plan
                      # injetado no system prompt do agent

# ─── Utils ───────────────────────────────────────────────────────

def mostrar_diff(original, novo, caminho):
    linhas_orig = original.splitlines(keepends=True)
    linhas_novo = novo.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        linhas_orig,
        linhas_novo,
        fromfile=f"original/{caminho}",
        tofile=f"novo/{caminho}"
    ))

    if not diff:
        print(f"{YELLOW}Nenhuma alteração detectada.{RESET}")
        return

    print(f"\n{BOLD}{'─'*60}{RESET}")
    print(f"{BOLD}Alterações propostas em: {caminho}{RESET}")
    print(f"{BOLD}{'─'*60}{RESET}")

    for linha in diff:
        if linha.startswith("+") and not linha.startswith("+++"):
            print(f"{GREEN}{linha}{RESET}", end="")
        elif linha.startswith("-") and not linha.startswith("---"):
            print(f"{RED}{linha}{RESET}", end="")
        else:
            print(linha, end="")

    print(f"\n{BOLD}{'─'*60}{RESET}\n")


def _extensao_suportada(caminho: Path) -> bool:
    exts = {
        ".kt", ".java", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
        ".toml", ".md", ".txt", ".xml", ".gradle", ".properties", ".sh",
        ".sql", ".html", ".css", ".env", ".kts",
    }
    return caminho.suffix.lower() in exts or caminho.name in {"CLAUDE_FILES.txt", "Makefile", "Dockerfile"}


def carregar_arquivo(caminho_str: str, silencioso: bool = False) -> bool:
    caminho_str = caminho_str.strip().replace("'", "").replace('"', "")
    caminho = Path(caminho_str)

    # Tenta relativo ao BASE_DIR se não existir como fornecido
    if not caminho.exists():
        alt = BASE_DIR / caminho
        if alt.exists():
            caminho = alt
        else:
            if not silencioso:
                print(f"{RED}Arquivo não encontrado: {caminho_str}{RESET}")
            return False

    if not _extensao_suportada(caminho):
        if not silencioso:
            print(f"{YELLOW}Extensão ignorada: {caminho}{RESET}")
        return False

    try:
        conteudo = caminho.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        if not silencioso:
            print(f"{RED}Erro ao ler {caminho}: {e}{RESET}")
        return False

    chave = str(caminho)
    arquivos_carregados[chave] = conteudo
    if not silencioso:
        print(f"{GREEN}✓ {chave}  ({len(conteudo):,} chars){RESET}")
    return True


def carregar_diretorio(dir_str: str, silencioso: bool = False) -> int:
    """Carrega recursivamente todos os arquivos de texto de um diretório."""
    diretorio = Path(dir_str.strip())
    if not diretorio.is_absolute():
        diretorio = BASE_DIR / diretorio
    if not diretorio.is_dir():
        if not silencioso:
            print(f"{RED}Diretório não encontrado: {diretorio}{RESET}")
        return 0
    total = 0
    for arq in sorted(diretorio.rglob("*")):
        if (arq.is_file()
                and _extensao_suportada(arq)
                and not _ignorar_caminho(arq)
                and ".bak" not in arq.suffixes):
            if carregar_arquivo(str(arq), silencioso=True):
                total += 1
    if not silencioso:
        print(f"{GREEN}✓ {total} arquivo(s) carregados de: {diretorio}{RESET}")
    return total


def carregar_claude_files(caminho_cf: Path | None = None) -> int:
    """
    Lê CLAUDE_FILES.txt e carrega cada entrada.
    Suporta:
      - arquivo individual:   ms-mobile/app/src/.../Foo.kt
      - diretório (barra):    ms-mobile/app/src/main/java/br/.../model/
      - glob simples:         ms-mobile/**/*.kt  (apenas ** e *)
      - comentários:          # linhas começando com #
    """
    if caminho_cf is None:
        for candidato in [BASE_DIR / "CLAUDE_FILES.txt",
                          BASE_DIR / "ms-mobile" / "CLAUDE_FILES.txt"]:
            if candidato.exists():
                caminho_cf = candidato
                break

    if caminho_cf is None or not caminho_cf.exists():
        return 0

    # Garante que o próprio CLAUDE_FILES.txt esteja carregado
    chave_cf = str(caminho_cf)
    if chave_cf not in arquivos_carregados:
        arquivos_carregados[chave_cf] = caminho_cf.read_text(encoding="utf-8", errors="replace")

    total = 0
    linhas = caminho_cf.read_text(encoding="utf-8", errors="replace").splitlines()

    for linha in linhas:
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue

        alvo = Path(linha)
        if not alvo.is_absolute():
            alvo = BASE_DIR / alvo

        if alvo.is_dir():
            total += carregar_diretorio(str(alvo), silencioso=True)
        elif "*" in linha:
            # Glob relativo ao BASE_DIR
            partes = linha.replace("\\", "/").split("/", 1)
            raiz   = BASE_DIR / partes[0] if len(partes) > 1 else BASE_DIR
            padrao = partes[1]            if len(partes) > 1 else partes[0]
            for arq in sorted(raiz.glob(padrao)):
                if arq.is_file() and _extensao_suportada(arq):
                    if carregar_arquivo(str(arq), silencioso=True):
                        total += 1
        else:
            if carregar_arquivo(str(alvo), silencioso=True):
                total += 1

    return total


def auto_carregar_contexto():
    """
    Carrega APENAS o esqueleto do projeto:
      - Arquivos de contexto/planejamento (CONTEXT.md, ARCHITECTURE.md, CLAUDE_FILES.txt…)
      - /spec e /spec/tasks (escopo e tarefas)
      - /docs e /skills (documentação)
      - NÃO carrega código-fonte automaticamente — use /arquivo ou /busca para isso
    """
    print(f"\n{CYAN}{'─'*60}{RESET}")
    print(f"{CYAN}  Carregando esqueleto do projeto…{RESET}")
    print(f"{CYAN}{'─'*60}{RESET}")

    skeleton = [
        "CONTEXT.md", "ARCHITECTURE.md", "README.md",
        "CLAUDE_FILES.txt",
        "ms-mobile/CONTEXT.md", "ms-mobile/CLAUDE_FILES.txt",
        "ms-api/CONTEXT.md",   "ms-api/CLAUDE_FILES.txt",
    ]
    for nome in skeleton:
        if carregar_arquivo(nome, silencioso=True):
            print(f"  {GREEN}✓{RESET} {nome}")

    for d in ["spec", "spec/tasks", "docs", "skills"]:
        p = BASE_DIR / d
        if p.is_dir():
            n = carregar_diretorio(str(p), silencioso=True)
            if n:
                print(f"  {GREEN}✓{RESET} /{d} → {n} arquivo(s)")

    tokens_est = sum(len(v) for v in arquivos_carregados.values()) // 4
    print(f"\n  {BOLD}Esqueleto: {len(arquivos_carregados)} arquivo(s) "
          f"≈ {tokens_est:,} tokens{RESET}")
    print(f"  {YELLOW}Use /busca <termo> para carregar arquivos de código relevantes.{RESET}")
    print(f"{CYAN}{'─'*60}{RESET}\n")


def salvar_arquivo(caminho, conteudo):
    with open(caminho, "w", encoding="utf-8") as f:
        f.write(conteudo)
    print(f"{GREEN}✓ Arquivo salvo: {caminho}{RESET}")


def backup_arquivo(caminho: str, conteudo: str):
    """Salva o conteúdo ORIGINAL em .bak ANTES de qualquer escrita."""
    bak = caminho + ".bak"
    # Usa sempre o conteúdo passado (original lido do disco antes da edição)
    with open(bak, "w", encoding="utf-8") as f:
        f.write(conteudo)
    print(f"{YELLOW}  ↳ Backup original salvo em: {bak}{RESET}")


# ─── JSON ────────────────────────────────────────────────────────
# FIX: extrai TODOS os blocos JSON e retorna o que contém "files"

def extrair_todos_json(texto: str) -> list[dict]:
    """
    Encontra todos os blocos {...} válidos no texto (inclusive dentro de ```json).
    Retorna lista de dicts parsed com sucesso.
    """
    # Remove marcações de bloco de código markdown
    texto_limpo = re.sub(r"```(?:json)?\s*", "", texto)
    texto_limpo = re.sub(r"```", "", texto_limpo)

    candidatos = []
    depth = 0
    inicio = None

    for i, ch in enumerate(texto_limpo):
        if ch == "{":
            if depth == 0:
                inicio = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and inicio is not None:
                trecho = texto_limpo[inicio:i+1]
                try:
                    parsed = json.loads(trecho)
                    candidatos.append(parsed)
                except json.JSONDecodeError:
                    pass
                inicio = None

    return candidatos


def extrair_json_agent(resposta: str) -> dict | None:
    """
    Dentre todos os JSONs encontrados na resposta, retorna
    o primeiro que contém a chave 'files' com lista válida.
    Imprime aviso detalhado se não encontrar.
    """
    candidatos = extrair_todos_json(resposta)

    if not candidatos:
        print(f"{RED}❌ Nenhum bloco JSON encontrado na resposta.{RESET}")
        return None

    for candidato in candidatos:
        if isinstance(candidato.get("files"), list):
            return candidato

    # Nenhum tinha "files" — mostra o que foi encontrado para debug
    print(f"{YELLOW}⚠ JSON encontrado, mas nenhum contém 'files'. "
          f"Chaves disponíveis: {[list(c.keys()) for c in candidatos]}{RESET}")
    return None


def validar_resposta(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if not isinstance(data.get("files"), list):
        return False
    for f in data["files"]:
        if "path" not in f or "content" not in f:
            return False
    return True


def resolver_caminho(caminho_agente: str) -> str | None:
    """
    O agent às vezes retorna caminho relativo (ex: 'app/src/.../Foo.kt')
    mas arquivos_carregados usa caminho absoluto ('/home/user/.../Foo.kt').
    Esta função resolve pelo sufixo mais longo em comum.
    """
    caminho_agente = caminho_agente.strip()

    # 1. Match exato
    if caminho_agente in arquivos_carregados:
        return caminho_agente

    # 2. Match por sufixo: percorre todos os caminhos carregados
    #    e retorna o que termina com o caminho fornecido pelo agent
    for caminho_real in arquivos_carregados:
        if caminho_real.endswith(caminho_agente) or caminho_agente.endswith(caminho_real):
            return caminho_real

    # 3. Match por nome de arquivo (último recurso)
    nome_agente = Path(caminho_agente).name
    candidatos = [c for c in arquivos_carregados if Path(c).name == nome_agente]
    if len(candidatos) == 1:
        return candidatos[0]
    if len(candidatos) > 1:
        print(f"{YELLOW}⚠ Múltiplos arquivos com nome '{nome_agente}':{RESET}")
        for i, c in enumerate(candidatos):
            print(f"  {i+1}. {c}")
        escolha = input(f"{BOLD}Qual aplicar? (número): {RESET}").strip()
        try:
            return candidatos[int(escolha) - 1]
        except (ValueError, IndexError):
            return None

    return None


# ─── Busca de arquivos por termo ─────────────────────────────────
# Token budget: reserva 20k para a resposta da IA
TOKEN_BUDGET_INPUT = 180_000
CHARS_POR_TOKEN    = 4        # estimativa conservadora

def tokens_estimados() -> int:
    total_chars = sum(len(v) for v in arquivos_carregados.values())
    return total_chars // CHARS_POR_TOKEN


def buscar_e_carregar(termo: str) -> int:
    """
    Busca arquivos cujo nome ou caminho contém o termo (case-insensitive).
    Lista os candidatos e pergunta quais carregar para não estourar contexto.
    """
    termo_lower = termo.lower()
    candidatos: list[Path] = []

    # Busca nos CLAUDE_FILES.txt listados
    for cf_path in BASE_DIR.rglob("CLAUDE_FILES.txt"):
        if _ignorar_caminho(cf_path):
            continue
        try:
            linhas = cf_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for linha in linhas:
            linha = linha.strip()
            if not linha or linha.startswith("#"):
                continue
            if termo_lower in linha.lower():
                p = Path(linha) if Path(linha).is_absolute() else BASE_DIR / linha
                if _ignorar_caminho(p):
                    continue
                if p.is_file() and p not in candidatos:
                    candidatos.append(p)
                elif p.is_dir():
                    for arq in sorted(p.rglob("*")):
                        if (arq.is_file() and _extensao_suportada(arq)
                                and not _ignorar_caminho(arq)
                                and arq not in candidatos):
                            candidatos.append(arq)

    # Busca também no disco (recursivo a partir do BASE_DIR)
    for arq in BASE_DIR.rglob("*"):
        if (arq.is_file() and _extensao_suportada(arq)
                and not _ignorar_caminho(arq)
                and termo_lower in arq.name.lower()
                and arq not in candidatos
                and ".bak" not in arq.suffixes):
            candidatos.append(arq)

    # Remove já carregados
    ja_carregados = set(arquivos_carregados.keys())
    novos = [c for c in candidatos if str(c) not in ja_carregados]

    if not novos:
        print(f"{YELLOW}Nenhum arquivo novo encontrado para '{termo}'.{RESET}")
        return 0

    # Mostra lista numerada com tamanho estimado
    print(f"\n{CYAN}Arquivos encontrados para '{termo}':{RESET}")
    for i, p in enumerate(novos, 1):
        try:
            sz = p.stat().st_size // 1024
        except Exception:
            sz = 0
        print(f"  {i:>3}. {p.relative_to(BASE_DIR)}  {YELLOW}({sz} KB){RESET}")

    tokens_atual = tokens_estimados()
    tokens_livre = TOKEN_BUDGET_INPUT - tokens_atual
    print(f"\n  {CYAN}Contexto atual: ≈{tokens_atual:,} tokens  |  "
          f"Disponível: ≈{tokens_livre:,} tokens{RESET}")
    print(f"  {YELLOW}Digite números separados por vírgula, 'todos', ou Enter para cancelar:{RESET} ", end="")

    escolha = input().strip().lower()
    if not escolha:
        return 0

    if escolha in ("todos", "all", "t"):
        selecionados = novos
    else:
        indices = []
        for parte in escolha.split(","):
            parte = parte.strip()
            if parte.isdigit():
                idx = int(parte) - 1
                if 0 <= idx < len(novos):
                    indices.append(idx)
        selecionados = [novos[i] for i in indices]

    carregados = 0
    for p in selecionados:
        tokens_agora = tokens_estimados()
        if tokens_agora >= TOKEN_BUDGET_INPUT:
            print(f"{RED}⚠ Limite de contexto atingido ({tokens_agora:,} tokens). "
                  f"Parando carregamento.{RESET}")
            break
        if carregar_arquivo(str(p), silencioso=False):
            carregados += 1

    return carregados


# ─── Diretórios a ignorar na busca ───────────────────────────────
DIRS_IGNORADOS = {
    "build", ".gradle", ".idea", ".git", "node_modules",
    "__pycache__", ".cache", "dist", "out", ".kotlin",
}

def _ignorar_caminho(p: Path) -> bool:
    """Retorna True se o caminho contém algum diretório ignorado."""
    return any(parte in DIRS_IGNORADOS for parte in p.parts)


# ─── Contexto ────────────────────────────────────────────────────

def montar_contexto_sistema() -> str:
    if not arquivos_carregados:
        return "Nenhum arquivo carregado."

    ctx = "Arquivos carregados:\n"
    for caminho, conteudo in arquivos_carregados.items():
        ctx += f"\n[{caminho}]\n{conteudo}\n"
    return ctx


# ─── Prompts por modo ────────────────────────────────────────────

def get_system_prompt():
    base_context = montar_contexto_sistema()

    if modo_atual == "ask":
        return f"""
{base_context}

Você é um assistente técnico.
Responda de forma clara e objetiva.
"""

    elif modo_atual == "plan":
        return f"""
{base_context}

Você é um arquiteto de software sênior.

OBJETIVO:
Ajudar a planejar:
- CONTEXT.md
- ARCHITECTURE.md
- CLAUDE_FILES.txt
- skills e documentação

REGRAS:
- Responder em JSON válido
- Estruturar em seções claras
- Criar perguntas inteligentes
- Sugerir melhorias estruturais
- Pensar como sistema escalável

Formato:
{{
  "sections": [
    {{
      "title": "nome",
      "items": ["..."]
    }}
  ],
  "suggestions": []
}}
"""

    elif modo_atual == "agent":
        secao_plano = f"""
PLANO DEFINIDO (resultado do planejamento anterior):
{plano_ativo}

""" if plano_ativo else ""

        return f"""
{base_context}

{secao_plano}Você é um agente de código. Sua ÚNICA forma de responder é com JSON puro.

REGRAS ABSOLUTAS — VIOLÁ-LAS CAUSA FALHA NO SISTEMA:
1. Responda EXCLUSIVAMENTE com um único objeto JSON
2. NÃO use blocos de código markdown (sem ``` ou ```json)
3. NÃO escreva texto, títulos, tabelas ou explicações fora do JSON
4. NÃO divida a resposta em múltiplos JSONs
5. O campo "explanation" dentro do JSON é o único lugar para texto
6. Sempre forneça o conteúdo COMPLETO do arquivo, nunca parcial
7. Para EDITAR: use o caminho exato como aparece nos arquivos carregados
8. Para CRIAR arquivo novo: use o caminho relativo correto — o sistema perguntará confirmação antes de criar

FORMATO OBRIGATÓRIO (único objeto, sem nada antes ou depois):
{{
  "files": [
    {{
      "path": "caminho/exato/como/aparece/nos/arquivos/carregados",
      "content": "conteúdo completo do arquivo aqui"
    }}
  ],
  "explanation": "descrição das mudanças em texto livre aqui"
}}

EXEMPLO DE RESPOSTA VÁLIDA:
{{
  "files": [
    {{
      "path": "app/src/main/java/br/com/exemplo/di/AppModule.kt",
      "content": "package br.com.exemplo.di\\n\\nval appModule = module {{\\n    single {{ MyClass() }}\\n}}"
    }}
  ],
  "explanation": "Migrei factory para single no AppModule para melhorar performance."
}}
"""

    return base_context


# ─── Janela de contexto por modelo ──────────────────────────────
CONTEXT_WINDOW = {
    "claude-3-haiku-20240307":      200_000,
    "claude-3-5-haiku-20241022":    200_000,
    "claude-3-sonnet-20240229":     200_000,
    "claude-3-5-sonnet-20241022":   200_000,
    "claude-3-5-sonnet-20240620":   200_000,
    "claude-3-opus-20240229":       200_000,
    "claude-opus-4-5":              200_000,
    "claude-sonnet-4-5":            200_000,
}
tokens_usados_total = 0  # acumulado da sessão

def get_context_window() -> int:
    for prefixo, janela in CONTEXT_WINDOW.items():
        if MODEL.startswith(prefixo) or MODEL == prefixo:
            return janela
    return 200_000  # fallback seguro


def exibir_tokens(input_tokens: int, output_tokens: int):
    global tokens_usados_total

    tokens_nesta_chamada = input_tokens + output_tokens
    tokens_usados_total += output_tokens  # só output acumula de fato no histórico

    janela = get_context_window()
    disponiveis = janela - input_tokens   # input já inclui todo o contexto atual
    pct_usado = (input_tokens / janela) * 100

    # Cor da barra de uso
    if pct_usado < 50:
        cor = GREEN
    elif pct_usado < 80:
        cor = YELLOW
    else:
        cor = RED

    print(
        f"{YELLOW}  ┌─ tokens ─────────────────────────────────────┐{RESET}\n"
        f"{YELLOW}  │{RESET} entrada: {CYAN}{input_tokens:>7,}{RESET}  saída: {CYAN}{output_tokens:>6,}{RESET}  total: {CYAN}{tokens_nesta_chamada:>7,}{RESET} {YELLOW}│{RESET}\n"
        f"{YELLOW}  │{RESET} contexto: {cor}{input_tokens:>6,}{RESET}/{janela:,}  "
        f"disponível: {cor}{disponiveis:>7,}{RESET} ({cor}{100-pct_usado:.1f}% livre{RESET}) {YELLOW}│{RESET}\n"
        f"{YELLOW}  └───────────────────────────────────────────────┘{RESET}"
    )


# ─── IA ──────────────────────────────────────────────────────────

def perguntar_ia(mensagem):
    historico.append({"role": "user", "content": mensagem})

    # Guarda preventivo: avisa se o contexto está próximo do limite
    tokens_est = tokens_estimados()
    if tokens_est > TOKEN_BUDGET_INPUT:
        print(f"{RED}⚠ Contexto estimado ({tokens_est:,} tokens) excede o limite seguro "
              f"({TOKEN_BUDGET_INPUT:,}).\n"
              f"  Use /limpar para remover arquivos ou /busca com menos arquivos.{RESET}")
        historico.pop()
        return ""

    print(f"\n{CYAN}Modo: {modo_atual.upper()} | Claude pensando… "
          f"(≈{tokens_est:,} tokens no contexto){RESET}")

    # max_tokens alto para suportar arquivos Kotlin/Java grandes
    MAX_TOKENS = 16_000

    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=get_system_prompt(),
        messages=historico,
    )

    resposta = response.content[0].text
    historico.append({"role": "assistant", "content": resposta})

    # Alerta se a resposta foi cortada por limite de tokens
    if response.stop_reason == "max_tokens":
        print(f"{RED}⚠ RESPOSTA TRUNCADA: o modelo atingiu o limite de {MAX_TOKENS} tokens.{RESET}")
        print(f"{YELLOW}  → No modo agent, JSON incompleto = falha. Peça para dividir em menos arquivos.{RESET}")

    exibir_tokens(response.usage.input_tokens, response.usage.output_tokens)

    return resposta


# ─── Edição ──────────────────────────────────────────────────────

def processar_edicao(resposta):
    if modo_atual != "agent":
        # Em ask/plan, salva a resposta como plano ativo para o agent usar depois
        global plano_ativo
        plano_ativo = resposta[-3000:]  # últimas 3000 chars (resumo suficiente)
        return

    # FIX: usa extrator robusto que procura especificamente o JSON com "files"
    data = extrair_json_agent(resposta)

    if not data or not validar_resposta(data):
        print(f"{YELLOW}⚠ Resposta do agente não contém alterações de arquivo.{RESET}")
        print(f"{YELLOW}  Dica: tente reformular ou adicionar 'responda só em JSON'{RESET}")
        return

    explanation = data.get("explanation", "")
    if explanation:
        print(f"\n{CYAN}📝 {explanation}{RESET}")

    # Resposta informativa sem arquivos (ex: perguntas sobre o contexto)
    if not data["files"]:
        return

    for file in data["files"]:
        caminho_agente = file["path"].strip()
        novo_codigo    = file["content"]

        # Resolve caminho relativo → absoluto via sufixo
        caminho_real = resolver_caminho(caminho_agente)

        # ── Arquivo NOVO (não carregado) ─────────────────────────
        if caminho_real is None:
            # Antes de oferecer criação, varre o disco procurando pelo nome
            nome_busca = Path(caminho_agente).name
            encontrados = [
                arq for arq in BASE_DIR.rglob(nome_busca)
                if arq.is_file() and not _ignorar_caminho(arq)
            ]

            if len(encontrados) == 1:
                # Encontrou exatamente um — carrega automaticamente e cai na edição
                caminho_real = str(encontrados[0])
                print(f"{CYAN}  ↳ Arquivo encontrado no disco: {Path(caminho_real).relative_to(BASE_DIR)}{RESET}")
                print(f"{CYAN}    Carregando para aplicar alteração com diff...{RESET}")
                carregar_arquivo(caminho_real, silencioso=True)
                # caminho_real agora está definido → cai no bloco de edição abaixo

            elif len(encontrados) > 1:
                # Ambíguo — mostra opções ao usuário
                print(f"{YELLOW}⚠ Múltiplos arquivos '{nome_busca}' encontrados:{RESET}")
                for i, e in enumerate(encontrados, 1):
                    print(f"  {i}. {e.relative_to(BASE_DIR)}")
                escolha = input(f"{BOLD}  Qual carregar? (número / n=ignorar): {RESET}").strip()
                if escolha.isdigit() and 1 <= int(escolha) <= len(encontrados):
                    caminho_real = str(encontrados[int(escolha) - 1])
                    carregar_arquivo(caminho_real, silencioso=True)
                    print(f"{CYAN}  ↳ Carregado: {Path(caminho_real).relative_to(BASE_DIR)}{RESET}")
                else:
                    print(f"{YELLOW}  Ignorado{RESET}")
                    continue

            else:
                # Não existe no disco — aí sim oferece criação
                print(f"\n{YELLOW}⚠ Arquivo não existe no disco: {BOLD}{caminho_agente}{RESET}")
                print(f"{CYAN}  A IA quer CRIAR este arquivo novo.{RESET}")
                confirmacao = input(
                    f"{BOLD}  Criar arquivo? [s=criar / n=ignorar / c=caminho manual]: {RESET}"
                ).strip().lower()

                if confirmacao in ("s", "sim", "y"):
                    destino = Path(caminho_agente)
                    if not destino.is_absolute():
                        destino = BASE_DIR / destino
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    destino.write_text(novo_codigo, encoding="utf-8")
                    arquivos_carregados[str(destino)] = novo_codigo
                    print(f"{GREEN}✓ Arquivo criado: {destino.relative_to(BASE_DIR)}{RESET}")

                elif confirmacao == "c":
                    novo_caminho = input(f"{BOLD}  Caminho destino: {RESET}").strip()
                    if novo_caminho:
                        destino = Path(novo_caminho)
                        if not destino.is_absolute():
                            destino = BASE_DIR / destino
                        destino.parent.mkdir(parents=True, exist_ok=True)
                        destino.write_text(novo_codigo, encoding="utf-8")
                        arquivos_carregados[str(destino)] = novo_codigo
                        print(f"{GREEN}✓ Arquivo criado em: {destino}{RESET}")
                    else:
                        print(f"{YELLOW}Ignorado (caminho vazio){RESET}")
                else:
                    print(f"{YELLOW}  Ignorado{RESET}")
                continue

        # ── Arquivo EXISTENTE (edição) ────────────────────────────
        if caminho_agente != caminho_real:
            print(f"{YELLOW}  ↳ Caminho resolvido: {caminho_agente} → {caminho_real}{RESET}")

        # Lê o conteúdo ATUAL do disco como base para o backup
        # (garante que o .bak sempre tem o original, mesmo se houve edição prévia)
        try:
            with open(caminho_real, "r", encoding="utf-8") as f:
                conteudo_disco = f.read()
        except FileNotFoundError:
            conteudo_disco = arquivos_carregados.get(caminho_real, "")

        original = arquivos_carregados[caminho_real]
        mostrar_diff(original, novo_codigo, caminho_real)

        confirmacao = input(f"{BOLD}Aplicar alteração em '{Path(caminho_real).name}'? [s/n]: {RESET}").lower()

        if confirmacao in ("s", "sim", "y"):
            # Backup do conteúdo ORIGINAL do disco antes de qualquer escrita
            backup_arquivo(caminho_real, conteudo_disco)
            salvar_arquivo(caminho_real, novo_codigo)
            arquivos_carregados[caminho_real] = novo_codigo
        else:
            print(f"{YELLOW}  Ignorado{RESET}")


# ─── CLI ─────────────────────────────────────────────────────────

def ajuda():
    print(f"""
{BOLD}Modos:{RESET}
  /modo ask      perguntas e planejamento livre
  /modo plan     planejamento estruturado em JSON
  /modo agent    edição/criação de arquivos

{BOLD}Arquivos:{RESET}
  /busca <termo>       busca arquivos por nome/caminho e pergunta quais carregar
  /arquivo <caminho>   carrega arquivo ou diretório inteiro (recursivo)
  /arquivos            lista arquivos carregados, agrupados por pasta
  /recarregar          recarrega esqueleto do projeto (limpa código-fonte)
  /limpar              remove TODOS os arquivos carregados da memória

{BOLD}Contexto:{RESET}
  /plano               exibe o plano ativo (capturado do modo ask/plan)
  /plano <texto>       define o plano manualmente

{BOLD}Sessão:{RESET}
  /reset               limpa histórico de conversa (mantém arquivos)
  /sair
""")


def main():
    global modo_atual, plano_ativo

    print(f"{CYAN}Claude CLI Multi-Mode iniciado{RESET}")
    auto_carregar_contexto()  # ← carrega CLAUDE_FILES.txt + estrutura do projeto

    while True:
        try:
            entrada = input(f"{BOLD}({modo_atual}) Você: {RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            break

        if not entrada:
            continue

        if entrada in ("/sair", "sair"):
            break

        elif entrada.startswith("/modo "):
            novo_modo = entrada.split(" ")[1]
            if novo_modo in ["ask", "plan", "agent"]:
                modo_anterior = modo_atual
                modo_atual = novo_modo
                print(f"{GREEN}Modo alterado para: {novo_modo}{RESET}")
                # Avisa que o plano foi preservado ao ir para agent
                if novo_modo == "agent" and plano_ativo:
                    print(f"{CYAN}  ✓ Plano do modo {modo_anterior} foi preservado e será usado pelo agent.{RESET}")
                    print(f"{CYAN}    Use /plano para visualizar.{RESET}")
            else:
                print(f"{RED}Modo inválido{RESET}")

        elif entrada == "/ajuda":
            ajuda()

        elif entrada.startswith("/busca "):
            buscar_e_carregar(entrada[7:].strip())

        elif entrada.startswith("/arquivo "):
            alvo = entrada[9:].strip()
            p = Path(alvo) if Path(alvo).is_absolute() else BASE_DIR / alvo
            if p.is_dir():
                carregar_diretorio(str(p))
            else:
                carregar_arquivo(alvo)

        elif entrada == "/arquivos":
            if not arquivos_carregados:
                print(f"{YELLOW}Nenhum arquivo carregado.{RESET}")
            else:
                from collections import defaultdict
                grupos: dict[str, list[str]] = defaultdict(list)
                for c in sorted(arquivos_carregados):
                    grupos[str(Path(c).parent)].append(Path(c).name)
                for pai, nomes in sorted(grupos.items()):
                    print(f"  {CYAN}{pai}/{RESET}")
                    for n in nomes:
                        print(f"    {GREEN}•{RESET} {n}")
                print(f"\n  {BOLD}Total: {len(arquivos_carregados)} arquivo(s){RESET}")

        elif entrada == "/recarregar":
            arquivos_carregados.clear()
            auto_carregar_contexto()

        elif entrada == "/plano":
            if plano_ativo:
                print(f"\n{CYAN}─── Plano ativo ───{RESET}\n{plano_ativo}\n")
            else:
                print(f"{YELLOW}Nenhum plano ativo. Use o modo ask/plan primeiro.{RESET}")

        elif entrada.startswith("/plano "):
            plano_ativo = entrada[7:].strip()
            print(f"{GREEN}✓ Plano definido manualmente.{RESET}")

        elif entrada == "/limpar":
            arquivos_carregados.clear()

        elif entrada == "/reset":
            historico.clear()
            print(f"{YELLOW}Histórico resetado{RESET}")

        else:
            resposta = perguntar_ia(entrada)

            if modo_atual == "agent":
                # No modo agent o JSON bruto não é útil para o usuário.
                # A explanation e o diff são exibidos dentro de processar_edicao.
                processar_edicao(resposta)
            else:
                print(f"\n{CYAN}Claude:{RESET} {resposta}\n")
                processar_edicao(resposta)


if __name__ == "__main__":
    main()
