#!/usr/bin/env python3
"""
Claude Code Assistant - Terminal Interativo (Multi-Mode: ask | plan | agent)
Auto-carrega: CONTEXT.md, CLAUDE_FILES.txt, /spec, /spec/tasks/*, /docs, /skills
"""

import anthropic
import os
from dotenv import load_dotenv
from pathlib import Path
import difflib
import re
import json

# ─── Setup ───────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(dotenv_path=BASE_DIR / ".env")

API_KEY = os.getenv("API_KEY")
MODEL   = os.getenv("MODEL", "claude-haiku-4-5-20251001")

if not API_KEY:
    raise ValueError("❌ API_KEY não encontrada no .env")

client = anthropic.Anthropic(api_key=API_KEY)

# ─── Cores ───────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"
BLUE   = "\033[94m"
MAGENTA = "\033[95m"

# ─── Extensões permitidas ─────────────────────────────────────────
EXTENSOES_TEXTO = {
    ".md", ".txt", ".py", ".js", ".ts", ".jsx", ".tsx",
    ".json", ".yaml", ".yml", ".toml", ".env.example",
    ".html", ".css", ".sh", ".sql", ".xml", ".csv",
    ".java", ".go", ".rs", ".rb", ".php", ".c", ".cpp", ".h",
}

# ─── Estado ──────────────────────────────────────────────────────
historico          = []
arquivos_carregados = {}   # { caminho_relativo: conteudo }
contexto_projeto   = {}    # { categoria: { caminho: conteudo } }
modo_atual         = "ask" # ask | plan | agent

# ─── Carregamento automático de projeto ──────────────────────────

def _eh_texto(caminho: Path) -> bool:
    return caminho.suffix.lower() in EXTENSOES_TEXTO or caminho.name in {
        "Makefile", "Dockerfile", ".env.example", "CLAUDE_FILES.txt"
    }


def _ler_arquivo(caminho: Path) -> str | None:
    try:
        return caminho.read_text(encoding="utf-8")
    except Exception as e:
        print(f"{YELLOW}⚠ Não foi possível ler {caminho}: {e}{RESET}")
        return None


def _registrar(categoria: str, caminho: Path, conteudo: str):
    rel = str(caminho.relative_to(BASE_DIR))
    contexto_projeto.setdefault(categoria, {})[rel] = conteudo
    arquivos_carregados[rel] = conteudo


def _carregar_arquivo_unico(caminho: Path, categoria: str) -> bool:
    if caminho.exists() and caminho.is_file() and _eh_texto(caminho):
        conteudo = _ler_arquivo(caminho)
        if conteudo is not None:
            _registrar(categoria, caminho, conteudo)
            return True
    return False


def _carregar_diretorio(diretorio: Path, categoria: str, recursivo: bool = True):
    """Carrega todos os arquivos de texto de um diretório."""
    if not diretorio.exists() or not diretorio.is_dir():
        return 0

    padrao = "**/*" if recursivo else "*"
    contador = 0
    for arquivo in sorted(diretorio.glob(padrao)):
        if arquivo.is_file() and _eh_texto(arquivo):
            conteudo = _ler_arquivo(arquivo)
            if conteudo is not None:
                _registrar(categoria, arquivo, conteudo)
                contador += 1
    return contador


def carregar_contexto_projeto():
    """
    Carrega automaticamente a estrutura padrão de projeto:

    BASE_DIR/
    ├── CONTEXT.md          → metadados e objetivos do projeto
    ├── CLAUDE_FILES.txt    → lista de arquivos relevantes para o Claude
    ├── spec/               → especificações gerais
    │   ├── *.md / *.txt
    │   └── tasks/          → tarefas individuais
    ├── docs/               → documentação do projeto
    └── skills/             → skills/padrões reutilizáveis
    """
    print(f"\n{CYAN}{BOLD}{'─'*60}{RESET}")
    print(f"{CYAN}{BOLD}  Carregando contexto do projeto em: {BASE_DIR}{RESET}")
    print(f"{CYAN}{BOLD}{'─'*60}{RESET}")

    total = 0

    # ── Arquivos raiz ────────────────────────────────────────────
    raiz_targets = {
        "CONTEXT.md":       "📋 Contexto",
        "CLAUDE_FILES.txt": "📄 Claude Files",
        "README.md":        "📖 README",
        "ARCHITECTURE.md":  "🏗  Arquitetura",
    }

    for nome, label in raiz_targets.items():
        caminho = BASE_DIR / nome
        if _carregar_arquivo_unico(caminho, "raiz"):
            total += 1
            print(f"  {GREEN}✓{RESET} {label}: {nome}")

    # ── CLAUDE_FILES.txt: carrega arquivos listados ───────────────
    claude_files_path = BASE_DIR / "CLAUDE_FILES.txt"
    if claude_files_path.exists():
        _processar_claude_files(claude_files_path)

    # ── /spec ─────────────────────────────────────────────────────
    spec_dir = BASE_DIR / "spec"
    n = _carregar_diretorio(spec_dir, "spec", recursivo=False)
    if n:
        total += n
        print(f"  {GREEN}✓{RESET} 📐 Spec: {n} arquivo(s) em /spec")

    # ── /spec/tasks/* ─────────────────────────────────────────────
    tasks_dir = spec_dir / "tasks"
    n = _carregar_diretorio(tasks_dir, "tasks", recursivo=True)
    if n:
        total += n
        print(f"  {GREEN}✓{RESET} 📝 Tasks: {n} arquivo(s) em /spec/tasks")

    # ── /docs ─────────────────────────────────────────────────────
    docs_dir = BASE_DIR / "docs"
    n = _carregar_diretorio(docs_dir, "docs", recursivo=True)
    if n:
        total += n
        print(f"  {GREEN}✓{RESET} 📚 Docs: {n} arquivo(s) em /docs")

    # ── /skills ───────────────────────────────────────────────────
    skills_dir = BASE_DIR / "skills"
    n = _carregar_diretorio(skills_dir, "skills", recursivo=True)
    if n:
        total += n
        print(f"  {GREEN}✓{RESET} 🛠  Skills: {n} arquivo(s) em /skills")

    print(f"\n  {BOLD}Total: {total} arquivo(s) carregado(s){RESET}")
    print(f"{CYAN}{'─'*60}{RESET}\n")


def _processar_claude_files(caminho: Path):
    """
    Lê CLAUDE_FILES.txt e carrega cada arquivo listado.
    Suporta linhas como:
        src/main.py
        src/utils/helper.py
        # comentários são ignorados
    """
    conteudo = _ler_arquivo(caminho)
    if not conteudo:
        return

    carregados = 0
    for linha in conteudo.splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#"):
            continue

        alvo = BASE_DIR / linha
        if alvo.exists() and alvo.is_file() and _eh_texto(alvo):
            texto = _ler_arquivo(alvo)
            if texto is not None:
                _registrar("claude_files", alvo, texto)
                carregados += 1

    if carregados:
        print(f"  {GREEN}✓{RESET} 📎 Claude Files extras: {carregados} arquivo(s)")


# ─── Utils ───────────────────────────────────────────────────────

def mostrar_diff(original, novo, caminho):
    linhas_orig = original.splitlines(keepends=True)
    linhas_novo = novo.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        linhas_orig, linhas_novo,
        fromfile=f"original/{caminho}",
        tofile=f"novo/{caminho}",
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


def carregar_arquivo_manual(caminho_str: str):
    caminho = Path(caminho_str.strip().replace("'", "").replace('"', ""))

    if not caminho.is_absolute():
        caminho = BASE_DIR / caminho

    if not caminho.exists():
        print(f"{RED}Arquivo não encontrado: {caminho}{RESET}")
        return False

    if not _eh_texto(caminho):
        print(f"{YELLOW}Extensão não suportada: {caminho.suffix}{RESET}")
        return False

    conteudo = _ler_arquivo(caminho)
    if conteudo is None:
        return False

    _registrar("manual", caminho, conteudo)
    print(f"{GREEN}✓ Arquivo carregado: {caminho}{RESET}")
    return True


def salvar_arquivo(caminho: str, conteudo: str):
    Path(caminho).write_text(conteudo, encoding="utf-8")
    print(f"{GREEN}✓ Arquivo salvo: {caminho}{RESET}")


def backup_arquivo(caminho: str, conteudo: str):
    Path(caminho + ".bak").write_text(conteudo, encoding="utf-8")


# ─── JSON ────────────────────────────────────────────────────────

def limpar_json_bruto(resposta: str) -> str:
    # Remove blocos de código markdown
    resposta = re.sub(r"```(?:json)?\s*", "", resposta)
    resposta = re.sub(r"```", "", resposta)
    match = re.search(r"\{[\s\S]*\}", resposta)
    return match.group(0) if match else resposta


def extrair_json(resposta: str) -> dict | None:
    try:
        return json.loads(limpar_json_bruto(resposta))
    except Exception as e:
        print(f"{RED}❌ JSON inválido: {e}{RESET}")
        return None


def validar_resposta_agent(data: dict) -> bool:
    return (
        isinstance(data, dict) and
        "files" in data and
        isinstance(data["files"], list) and
        all("path" in f and "content" in f for f in data["files"])
    )


# ─── Contexto do sistema ─────────────────────────────────────────

def montar_contexto_sistema() -> str:
    if not arquivos_carregados:
        return "Nenhum arquivo carregado."

    secoes = []

    # Agrupa por categoria para melhor legibilidade
    categorias_labels = {
        "raiz":        "📋 ARQUIVOS RAIZ DO PROJETO",
        "claude_files":"📎 ARQUIVOS LISTADOS EM CLAUDE_FILES.TXT",
        "spec":        "📐 ESPECIFICAÇÕES (/spec)",
        "tasks":       "📝 TAREFAS (/spec/tasks)",
        "docs":        "📚 DOCUMENTAÇÃO (/docs)",
        "skills":      "🛠  SKILLS (/skills)",
        "manual":      "📂 ARQUIVOS CARREGADOS MANUALMENTE",
    }

    for categoria, label in categorias_labels.items():
        arquivos_cat = contexto_projeto.get(categoria, {})
        if not arquivos_cat:
            continue

        secao = [f"\n{'═'*60}", f"  {label}", f"{'═'*60}"]
        for caminho, conteudo in arquivos_cat.items():
            secao.append(f"\n[{caminho}]\n{conteudo}")
        secoes.append("\n".join(secao))

    return "\n".join(secoes) if secoes else "Nenhum arquivo carregado."


# ─── Prompts por modo ────────────────────────────────────────────

def get_system_prompt() -> str:
    base_context = montar_contexto_sistema()

    if modo_atual == "ask":
        return f"""\
Você é um assistente técnico especializado neste projeto.

CONTEXTO DO PROJETO:
{base_context}

INSTRUÇÕES:
- Responda com base nos arquivos carregados quando relevante
- Seja direto, preciso e técnico
- Use markdown para formatar respostas quando útil
- Se referenciar um arquivo, cite o caminho exato
"""

    elif modo_atual == "plan":
        return f"""\
Você é um arquiteto de software sênior analisando este projeto.

CONTEXTO DO PROJETO:
{base_context}

OBJETIVO:
Ajudar a planejar e estruturar:
- CONTEXT.md e ARCHITECTURE.md
- CLAUDE_FILES.txt
- /spec e /spec/tasks
- /skills e /docs

REGRAS:
- Responder em JSON válido
- Criar seções claras e acionáveis
- Sugerir melhorias de arquitetura
- Identificar lacunas na documentação
- Pensar em escalabilidade e manutenção

FORMATO DE RESPOSTA:
{{
  "analysis": "resumo do estado atual",
  "sections": [
    {{
      "title": "nome da seção",
      "items": ["item 1", "item 2"]
    }}
  ],
  "suggestions": ["sugestão 1", "sugestão 2"],
  "missing_files": ["arquivo sugerido 1"]
}}
"""

    elif modo_atual == "agent":
        return f"""\
Você é um agente de código autônomo trabalhando neste projeto.

CONTEXTO DO PROJETO:
{base_context}

REGRAS ABSOLUTAS:
1. Responder SOMENTE em JSON válido
2. Incluir APENAS arquivos já carregados no contexto
3. Sempre fornecer o código COMPLETO do arquivo (nunca parcial)
4. Nunca inventar caminhos de arquivos

FORMATO OBRIGATÓRIO:
{{
  "files": [
    {{
      "path": "caminho/exato/do/arquivo",
      "content": "conteúdo completo do arquivo"
    }}
  ],
  "explanation": "descrição das mudanças realizadas"
}}
"""

    return base_context


# ─── IA ──────────────────────────────────────────────────────────

def perguntar_ia(mensagem: str) -> str:
    historico.append({"role": "user", "content": mensagem})

    print(f"\n{CYAN}▶ Modo: {BOLD}{modo_atual.upper()}{RESET}{CYAN} | Aguardando Claude...{RESET}")

    response = client.messages.create(
        model=MODEL,
        max_tokens=8096,
        system=get_system_prompt(),
        messages=historico,
    )

    resposta = response.content[0].text
    historico.append({"role": "assistant", "content": resposta})

    tokens_in  = response.usage.input_tokens
    tokens_out = response.usage.output_tokens
    print(f"{YELLOW}  tokens: {tokens_in} in / {tokens_out} out{RESET}")

    return resposta


# ─── Processamento de edições (modo agent) ───────────────────────

def processar_edicao(resposta: str):
    if modo_atual != "agent":
        return

    data = extrair_json(resposta)
    if not data or not validar_resposta_agent(data):
        print(f"{YELLOW}⚠ Resposta do agente não contém alterações de arquivo válidas.{RESET}")
        return

    explanation = data.get("explanation", "")
    if explanation:
        print(f"\n{BLUE}📝 Explicação: {explanation}{RESET}")

    for file in data["files"]:
        caminho = file["path"].strip()
        novo_codigo = file["content"]

        if caminho not in arquivos_carregados:
            print(f"{YELLOW}⚠ Arquivo fora do contexto, ignorando: {caminho}{RESET}")
            continue

        original = arquivos_carregados[caminho]
        mostrar_diff(original, novo_codigo, caminho)

        confirmacao = input(f"{BOLD}Aplicar alteração em '{caminho}'? [s/n]: {RESET}").strip().lower()

        if confirmacao in ("s", "sim", "y", "yes"):
            backup_arquivo(caminho, original)
            salvar_arquivo(caminho, novo_codigo)
            arquivos_carregados[caminho] = novo_codigo
            # Atualiza também no contexto_projeto
            for cat in contexto_projeto.values():
                if caminho in cat:
                    cat[caminho] = novo_codigo
            print(f"{GREEN}✓ Aplicado! Backup em: {caminho}.bak{RESET}")
        else:
            print(f"{YELLOW}↩ Ignorado{RESET}")


# ─── CLI helpers ─────────────────────────────────────────────────

def listar_arquivos_carregados():
    if not contexto_projeto:
        print(f"{YELLOW}Nenhum arquivo carregado.{RESET}")
        return

    categorias_labels = {
        "raiz":        "📋 Raiz",
        "claude_files":"📎 Claude Files",
        "spec":        "📐 Spec",
        "tasks":       "📝 Tasks",
        "docs":        "📚 Docs",
        "skills":      "🛠  Skills",
        "manual":      "📂 Manual",
    }

    for categoria, label in categorias_labels.items():
        arquivos_cat = contexto_projeto.get(categoria, {})
        if not arquivos_cat:
            continue
        print(f"\n  {BOLD}{label}{RESET}")
        for caminho in sorted(arquivos_cat):
            tamanho = len(arquivos_carregados[caminho])
            print(f"    {GREEN}•{RESET} {caminho} {YELLOW}({tamanho:,} chars){RESET}")

    print(f"\n  {BOLD}Total: {len(arquivos_carregados)} arquivo(s){RESET}")


def ajuda():
    print(f"""
{BOLD}{CYAN}Claude Code Assistant — Comandos{RESET}

{BOLD}Modos de operação:{RESET}
  {GREEN}/modo ask{RESET}     Perguntas técnicas livres
  {GREEN}/modo plan{RESET}    Planejamento de arquitetura (retorna JSON estruturado)
  {GREEN}/modo agent{RESET}   Agente de código (edita arquivos com diff + confirmação)

{BOLD}Arquivos:{RESET}
  {GREEN}/arquivo <caminho>{RESET}   Carrega arquivo manualmente
  {GREEN}/arquivos{RESET}            Lista todos os arquivos carregados (por categoria)
  {GREEN}/recarregar{RESET}          Recarrega o contexto completo do projeto
  {GREEN}/limpar{RESET}              Remove arquivos carregados manualmente

{BOLD}Sessão:{RESET}
  {GREEN}/reset{RESET}    Limpa histórico de conversa (mantém arquivos)
  {GREEN}/ajuda{RESET}    Exibe este menu
  {GREEN}/sair{RESET}     Encerra o assistente

{BOLD}Estrutura esperada do projeto:{RESET}
  CONTEXT.md           → Contexto e objetivos
  CLAUDE_FILES.txt     → Lista de arquivos relevantes
  spec/                → Especificações gerais
  spec/tasks/          → Tarefas individuais
  docs/                → Documentação
  skills/              → Skills e padrões
""")


# ─── Main ────────────────────────────────────────────────────────

def main():
    global modo_atual

    print(f"\n{CYAN}{BOLD}{'═'*60}{RESET}")
    print(f"{CYAN}{BOLD}   Claude Code Assistant  •  Multi-Mode CLI{RESET}")
    print(f"{CYAN}{BOLD}   Modelo: {MODEL}{RESET}")
    print(f"{CYAN}{BOLD}{'═'*60}{RESET}")

    # Carrega automaticamente ao iniciar
    carregar_contexto_projeto()

    print(f"{YELLOW}  Digite /ajuda para ver os comandos disponíveis{RESET}\n")

    while True:
        try:
            entrada = input(f"{BOLD}({modo_atual}) você: {RESET}").strip()
        except (KeyboardInterrupt, EOFError):
            print(f"\n{YELLOW}Encerrando...{RESET}")
            break

        if not entrada:
            continue

        # ── Comandos ──────────────────────────────────────────────
        if entrada in ("/sair", "sair", "exit", "quit"):
            print(f"{CYAN}Até mais! 👋{RESET}")
            break

        elif entrada.startswith("/modo "):
            modo = entrada.split(" ", 1)[1].strip()
            if modo in ("ask", "plan", "agent"):
                modo_atual = modo
                print(f"{GREEN}✓ Modo alterado para: {BOLD}{modo}{RESET}")
            else:
                print(f"{RED}Modo inválido. Use: ask | plan | agent{RESET}")

        elif entrada == "/ajuda":
            ajuda()

        elif entrada.startswith("/arquivo "):
            carregar_arquivo_manual(entrada[9:])

        elif entrada == "/arquivos":
            listar_arquivos_carregados()

        elif entrada == "/recarregar":
            arquivos_carregados.clear()
            contexto_projeto.clear()
            carregar_contexto_projeto()

        elif entrada == "/limpar":
            manual = contexto_projeto.pop("manual", {})
            for caminho in manual:
                arquivos_carregados.pop(caminho, None)
            print(f"{YELLOW}Arquivos manuais removidos ({len(manual)}).{RESET}")

        elif entrada == "/reset":
            historico.clear()
            print(f"{YELLOW}✓ Histórico de conversa resetado.{RESET}")

        # ── IA ────────────────────────────────────────────────────
        else:
            resposta = perguntar_ia(entrada)
            print(f"\n{CYAN}Claude:{RESET}\n{resposta}\n")
            processar_edicao(resposta)


if __name__ == "__main__":
    main()
