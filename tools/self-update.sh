#!/usr/bin/env bash
# SessionFlow — checa se há versão nova no remoto (main) e, se houver,
# atualiza e reconstrói os containers sozinho. Pensado pra rodar em loop
# (ver tools/self-update-loop.sh) num host de instalação de amigo, cujo
# `origin` aponta pro mirror PÚBLICO (daraujo85/sessionflow-release) — sem
# precisar de token/deploy key pra isso.
#
# Seguro por padrão: só atualiza se a árvore de trabalho estiver limpa (sem
# mudanças locais) e o fast-forward for possível; nunca reseta/descarta nada
# na força. Se algo estiver "sujo", loga e sai sem mexer (evita apagar
# customização local por engano).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

BRANCH="${SESSIONFLOW_UPDATE_BRANCH:-main}"
LOG() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

if [ -n "$(git status --porcelain)" ]; then
  LOG "árvore de trabalho suja — pulando auto-update (resolva manualmente antes)."
  exit 0
fi

git fetch origin "$BRANCH" --quiet

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"

if [ "$LOCAL" = "$REMOTE" ]; then
  LOG "já na última versão ($LOCAL)."
  exit 0
fi

LOG "nova versão encontrada: $LOCAL -> $REMOTE. Atualizando…"
git merge --ff-only "origin/$BRANCH"

LOG "reconstruindo containers (docker compose --profile app up -d --build)…"
docker compose --profile app up -d --build

# Worker roda no HOST (fora do docker, precisa de tmux pra gerenciar as
# sessões dos agentes) — não é pego pelo `docker compose up`. Se existir a
# sessão de infra padrão, reinicia o processo (Ctrl-C); o wrapper
# `while true; do ...; sleep 5; done` criado no onboarding sobe de novo
# sozinho, e o `uv run` já resincroniza as deps (pyproject/uv.lock) na hora.
# Best-effort: sem tmux ou sem essa sessão, não é erro — só não reinicia.
if command -v tmux >/dev/null 2>&1 && tmux has-session -t sessionflow-worker 2>/dev/null; then
  LOG "reiniciando o worker (sessão tmux sessionflow-worker)…"
  tmux send-keys -t sessionflow-worker C-c "" Enter || true
fi

LOG "atualizado com sucesso pra $(git rev-parse --short HEAD)."
