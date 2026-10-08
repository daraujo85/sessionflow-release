#!/usr/bin/env bash
# SessionFlow — "um comando só" pra instalação de amigo (Heverton, Alvarenga,
# Lucas…): atualiza o clone, sincroniza deps e sobe o worker + auto-update.
#
#   ./tools/start-worker.sh
#
# O que faz, em ordem:
#   1. git pull (ff-only) do origin — só com árvore LIMPA; suja = avisa e segue
#      com a versão local (nunca descarta mudança).
#   2. uv sync no worker/ (instala/atualiza deps do pyproject/uv.lock).
#   3. Sobe o worker:
#        - se existe a unit systemd `sessionflow-worker` → restart nela;
#        - senão → sessão tmux `sessionflow-worker` com loop que se reergue
#          sozinho (o self-update.sh reinicia via Ctrl-C nessa sessão).
#   4. Garante a sessão tmux `sessionflow-autoupdate` (tools/self-update-loop.sh)
#      → próximas versões chegam sozinhas, sem precisar rodar isto de novo.
#
# Rodar de novo é seguro (idempotente): reinicia o worker na versão nova.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$(pwd)"
BRANCH="${SESSIONFLOW_UPDATE_BRANCH:-main}"
LOG() { echo "[sessionflow] $*"; }
die() { echo "[sessionflow] ERRO: $*" >&2; exit 1; }

# uv/tmux instalados pelo usuário costumam ficar fora do PATH de shells não
# interativos (mesma classe de bug de host_metrics.installed_agents).
export PATH="$PATH:$HOME/.local/bin:$HOME/.cargo/bin:/usr/local/bin"
command -v uv >/dev/null 2>&1 || die "uv não encontrado — instale: curl -LsSf https://astral.sh/uv/install.sh | sh"
[ -f "$ROOT/.env" ] || die "faltou o $ROOT/.env (MONGO_URI_HOST/RABBITMQ_URI_HOST) — veja docs/lucas-standalone-setup.md"

# 1. Atualiza
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  LOG "há mudanças locais — pulando git pull (rodando a versão atual $(git rev-parse --short HEAD))."
else
  LOG "atualizando ($BRANCH)…"
  git pull --ff-only --quiet origin "$BRANCH" || LOG "git pull falhou — seguindo com a versão local."
fi
LOG "versão: $(git rev-parse --short HEAD)"

# 2. Deps
LOG "sincronizando dependências do worker…"
(cd "$ROOT/worker" && uv sync -q)

# 3. Worker
if systemctl list-unit-files sessionflow-worker.service 2>/dev/null | grep -q sessionflow-worker; then
  LOG "reiniciando a unit systemd sessionflow-worker (pede sudo)…"
  sudo systemctl restart sessionflow-worker
  systemctl is-active --quiet sessionflow-worker && LOG "worker ativo (systemd)." || die "worker não subiu — veja: journalctl -u sessionflow-worker -n 50"
  LOGS_HINT="journalctl -u sessionflow-worker -f"
else
  LOGS_HINT="tmux attach -t sessionflow-worker (sair: Ctrl-B depois D)"
  command -v tmux >/dev/null 2>&1 || die "tmux não encontrado — instale (ex.: sudo apt install tmux)"
  if tmux has-session -t sessionflow-worker 2>/dev/null; then
    # Ctrl-C mata o processo; o loop abaixo o sobe de novo já na versão nova.
    LOG "reiniciando o worker (tmux sessionflow-worker)…"
    tmux send-keys -t sessionflow-worker C-c
  else
    LOG "subindo o worker (tmux sessionflow-worker)…"
    tmux new-session -d -s sessionflow-worker -c "$ROOT/worker" \
      "while true; do uv run python -m sessionflow_worker; echo 'worker saiu — reiniciando em 5s'; sleep 5; done"
  fi
fi

# 4. Auto-update (best-effort: sem tmux, só não liga)
if command -v tmux >/dev/null 2>&1 && ! tmux has-session -t sessionflow-autoupdate 2>/dev/null; then
  LOG "ligando auto-update (tmux sessionflow-autoupdate, a cada 30min)…"
  tmux new-session -d -s sessionflow-autoupdate -c "$ROOT" './tools/self-update-loop.sh'
fi

LOG "pronto ✅ — em ~30s o computador aparece online no SessionFlow."
LOG "logs do worker: $LOGS_HINT"
