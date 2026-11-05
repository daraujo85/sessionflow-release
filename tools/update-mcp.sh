#!/usr/bin/env bash
# Mantém o servidor MCP do SessionFlow em sincronia com o repo — idempotente.
#
# Por que existe: o servidor MCP é um processo stdio FILHO do Claude Code. Ele
# carrega `tools/mcp_server.py` (e o `tools/sf` que ele importa) uma única vez,
# no início da sessão. Consequências:
#
#   • código novo no disco NÃO chega ao processo já em execução → é preciso
#     reiniciar o Claude Code;
#   • dependência nova não está no venv → nem reiniciando funciona.
#
# Sem este script, cada release que mexe no MCP exige 4 deploys manuais e as
# máquinas divergem em silêncio (o Mac com a tool nova, a Duck sem).
#
# Uso:
#   tools/update-mcp.sh           # sincroniza (deps + registro) e reporta
#   tools/update-mcp.sh --check   # só diagnostica: sai 1 se está desatualizado
#   tools/update-mcp.sh --quiet   # silencioso quando não há nada a fazer
#
# Pensado pra ser chamado no fim do tools/self-update.sh.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS="$REPO/tools"
VENV="$TOOLS/.venv-mcp"
SERVER="$TOOLS/mcp_server.py"
CLI="$TOOLS/sf"
REQS="$TOOLS/mcp-requirements.txt"
STATE="$VENV/.state"       # o que ESTE script deixou instalado
LOADED="$VENV/.loaded"     # o que o processo em execução realmente carregou
CLAUDE_JSON="${CLAUDE_CONFIG:-$HOME/.claude.json}"

# `uv` vive em ~/.local/bin, fora do PATH de shell não-interativo (ssh, cron,
# systemd — é assim que o self-update roda). `command -v uv` puro dá falso
# negativo e o fallback cai no `pip` de um venv que pode não ter pip (distro
# sem python3.x-venv). Mesma descoberta do install-mcp.sh, de propósito.
UV_BIN=""
find_uv() {
  if [ -n "$UV_BIN" ]; then return 0; fi
  for cand in "$(command -v uv 2>/dev/null || true)" \
              "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /usr/local/bin/uv /opt/homebrew/bin/uv; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then UV_BIN="$cand"; return 0; fi
  done
  return 1
}

MODE="sync"
QUIET=0
case "${1:-}" in
  -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
  --check) MODE="check" ;;
  --quiet) QUIET=1 ;;
esac

say()  { [ "$QUIET" = 1 ] && return 0; echo "$@"; }
warn() { echo "$@" >&2; }

# Hash do que importa para o comportamento do servidor: o próprio servidor e o
# CLI que ele carrega como módulo (tools/sf). Alterar qualquer um exige
# processo novo; só mexer no README não.
desired_hash() {
  [ -f "$SERVER" ] || { echo "sem-servidor"; return 0; }
  if [ -f "$CLI" ]; then
    cat "$SERVER" "$CLI" | shasum -a 256 2>/dev/null | cut -c1-16 \
      || cat "$SERVER" "$CLI" | sha256sum | cut -c1-16
  else
    shasum -a 256 "$SERVER" 2>/dev/null | cut -c1-16 || sha256sum "$SERVER" | cut -c1-16
  fi
}

reqs_hash() {
  [ -f "$REQS" ] || { echo "sem-reqs"; return 0; }
  shasum -a 256 "$REQS" 2>/dev/null | cut -c1-16 || sha256sum "$REQS" | cut -c1-16
}

state_hash() {
  [ -f "$STATE" ] || { echo ""; return 0; }
  sed -n 's/^code=//p' "$STATE"
}

state_reqs() {
  [ -f "$STATE" ] || { echo ""; return 0; }
  sed -n 's/^reqs=//p' "$STATE"
}

loaded_hash() {
  [ -f "$LOADED" ] || { echo ""; return 0; }
  tr -d '[:space:]' < "$LOADED"
}

venv_ok() { [ -x "$VENV/bin/python" ]; }

registrado() {
  [ -f "$CLAUDE_JSON" ] || return 1
  grep -q '"sessionflow"' "$CLAUDE_JSON" 2>/dev/null || return 1
}

# Reiniciar é a única forma de o agente enxergar código novo: o processo já
# aberto continua com o módulo antigo em memória.
needs_restart() {
  local want loaded
  want="$(desired_hash)"
  loaded="$(loaded_hash)"
  [ -n "$loaded" ] && [ "$loaded" != "$want" ]
}

status_line() {
  local want state req_now req_state
  want="$(desired_hash)"; state="$(state_hash)"
  req_now="$(reqs_hash)"; req_state="$(state_reqs)"
  if ! venv_ok || ! registrado; then
    echo "não instalado"
  elif [ "$state" != "$want" ]; then
    echo "desatualizado (servidor/CLI mudou)"
  elif [ "$req_state" != "$req_now" ]; then
    echo "desatualizado (dependências mudaram)"
  else
    echo "em dia"
  fi
}

# -- modo check: só diagnostica --------------------------------------------- #
if [ "$MODE" = "check" ]; then
  st="$(status_line)"
  echo "MCP: $st"
  if [ "$st" != "em dia" ]; then
    exit 1
  fi
  if needs_restart; then
    echo "MCP: processo em execução está defasado — reinicie o Claude Code para carregar a versão nova."
    exit 1
  fi
  exit 0
fi

# -- modo sync --------------------------------------------------------------- #
# Venv ausente: delega ao installer, que é o único que sabe criar venv do zero
# (uv ou venv+ensurepip). Daqui pra baixo o venv já existe.
if ! venv_ok; then
  say "MCP: não instalado — instalando (venv + registro)…"
  "$TOOLS/install-mcp.sh" >/dev/null 2>&1 || { warn "MCP: install-mcp.sh falhou"; exit 1; }
fi

changed=0

# Dependências: só toca no venv quando o requirements muda (o self-update roda
# a cada 30 min; reinstalar por rotina seria desperdício de rede).
if [ "$(state_reqs)" != "$(reqs_hash)" ]; then
  say "MCP: sincronizando dependências do venv…"
  PY="$VENV/bin/python"
  if find_uv; then
    "$UV_BIN" pip install --python "$PY" -r "$REQS" >/dev/null 2>&1 \
      || { warn "MCP: falha ao instalar dependências"; exit 1; }
  else
    "$PY" -m pip install --quiet -r "$REQS" >/dev/null 2>&1 \
      || { warn "MCP: falha ao instalar dependências"; exit 1; }
  fi
  changed=1
fi

# Registro: garante a entrada no ~/.claude.json (idempotente e barato; cobre o
# caso de alguém ter limpado o arquivo ou trocado de usuário).
if ! registrado; then
  say "MCP: registrando no Claude Code…"
  "$TOOLS/install-mcp.sh" >/dev/null 2>&1 || { warn "MCP: falha ao registrar"; exit 1; }
  changed=1
fi

# Estado: o que ESTE script instalou. O `.loaded` é escrito pelo próprio
# servidor quando sobe — comparar os dois revela processo defasado.
mkdir -p "$VENV"
printf 'code=%s\nreqs=%s\n' "$(desired_hash)" "$(reqs_hash)" > "$STATE"

if [ "$changed" = 1 ]; then
  say "MCP: sincronizado."
fi

if needs_restart; then
  say "MCP: o processo em execução ainda usa a versão anterior — reinicie o Claude Code para carregar a nova."
fi

exit 0
