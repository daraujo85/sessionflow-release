#!/usr/bin/env bash
# Instala/atualiza o servidor MCP do SessionFlow e o registra no Claude Code.
#
# Por que um script: o venv (.venv-mcp/) é gitignored, então "registrar o MCP"
# não pode depender de um caminho que já exista na máquina. Este script cria o
# venv, instala o SDK e grava a entrada em ~/.claude.json com caminhos ABSOLUTOS
# resolvidos a partir da localização real do repo.
#
# Uso:
#   tools/install-mcp.sh              # registra no Claude Code (~/.claude.json)
#   tools/install-mcp.sh --print      # só imprime o JSON (para colar à mão)
#   tools/install-mcp.sh --uninstall  # remove o registro
#
# Variável opcional SESSIONFLOW_MCP_REMOTE=<alias> fixa uma instância remota
# (credenciais em ~/.claude/secrets/sf-remotes.env) em vez da local.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO/tools/.venv-mcp"
SERVER="$REPO/tools/mcp_server.py"
CLAUDE_JSON="${CLAUDE_CONFIG:-$HOME/.claude.json}"
ENTRY_NAME="sessionflow"

PYTHON_BIN="${PYTHON:-python3}"

usage() { sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

ensure_venv() {
  if [ ! -x "$VENV/bin/python" ]; then
    echo "▸ criando venv em $VENV"
    # uv é o gerenciador usado no repo (mais rápido e já presente); fallback venv.
    if command -v uv >/dev/null 2>&1; then
      uv venv "$VENV" --python 3.12 >/dev/null
      uv pip install --python "$VENV/bin/python" mcp >/dev/null
    else
      "$PYTHON_BIN" -m venv "$VENV"
      "$VENV/bin/python" -m pip install --quiet --upgrade pip
      "$VENV/bin/python" -m pip install --quiet mcp
    fi
  fi
  "$VENV/bin/python" -c "import mcp" 2>/dev/null || {
    echo "▸ SDK mcp ausente no venv; instalando"
    if command -v uv >/dev/null 2>&1; then
      uv pip install --python "$VENV/bin/python" mcp >/dev/null
    else
      "$VENV/bin/python" -m pip install --quiet mcp
    fi
  }
}

json_entry() {
  "$VENV/bin/python" - "$VENV/bin/python" "$SERVER" "${SESSIONFLOW_MCP_REMOTE:-}" <<'PY'
import json, sys
python, server, remote = sys.argv[1:4]
entry = {"command": python, "args": [server]}
if remote:
    entry["env"] = {"SESSIONFLOW_MCP_REMOTE": remote}
print(json.dumps(entry, indent=2, ensure_ascii=False))
PY
}

uninstall() {
  [ -f "$CLAUDE_JSON" ] || { echo "sem $CLAUDE_JSON"; exit 0; }
  "$PYTHON_BIN" - "$CLAUDE_JSON" "$ENTRY_NAME" <<'PY'
import json, sys
path, name = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
servers = data.get("mcpServers") or {}
if servers.pop(name, None) is None:
    print(f"'{name}' não estava registrado")
else:
    data["mcpServers"] = servers
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    print(f"'{name}' removido de {path}")
PY
}

if [ "${1:-}" = "--uninstall" ]; then
  uninstall
  exit 0
fi

ensure_venv

if [ "${1:-}" = "--print" ]; then
  json_entry
  exit 0
fi

[ -f "$CLAUDE_JSON" ] || { echo "não achei $CLAUDE_JSON; use --print e cole à mão"; exit 1; }

# Backup antes de reescrever um arquivo de config global do usuário.
cp "$CLAUDE_JSON" "$CLAUDE_JSON.bak-sessionflow-mcp"

"$VENV/bin/python" - "$CLAUDE_JSON" "$ENTRY_NAME" "$VENV/bin/python" "$SERVER" \
    "${SESSIONFLOW_MCP_REMOTE:-}" <<'PY'
import json, sys

path, name, python, server, remote = sys.argv[1:6]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)

servers = data.setdefault("mcpServers", {})
entry = {"command": python, "args": [server]}
if remote:
    entry["env"] = {"SESSIONFLOW_MCP_REMOTE": remote}

before = servers.get(name)
servers[name] = entry

with open(path, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2, ensure_ascii=False)

action = "atualizado" if before else "registrado"
print(f"✅ '{name}' {action} em {path}")
print(f"   python: {python}")
print(f"   server: {server}")
if remote:
    print(f"   remoto: {remote}")
print("\nReinicie o Claude Code para carregar o servidor.")
PY
