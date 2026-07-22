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

# Fix defensivo: hosts que rodaram Docker Desktop (WSL2) antes de migrar pro
# Docker Engine nativo às vezes ficam com ~/.docker/config.json apontando pro
# credential-helper do Desktop ("desktop.exe"), que não existe mais nesse
# contexto — `docker compose build` falha ao puxar imagem pública com
# "error getting credentials". Reproduziu 2x no Duck Server; resetar aqui
# evita quebrar o auto-update sozinho no meio da madrugada.
DOCKER_CFG="$HOME/.docker/config.json"
if [ -f "$DOCKER_CFG" ] && grep -q '"credsStore"' "$DOCKER_CFG" 2>/dev/null; then
  LOG "~/.docker/config.json tem credsStore configurado — resetando (evita 'error getting credentials' em pull de imagem pública)."
  echo '{}' > "$DOCKER_CFG"
fi

LOG "reconstruindo containers (docker compose --profile app up -d --build)…"
export GIT_SHA="$(git rev-parse --short HEAD)"
# <épico>.<data do commit AAAAMMDD>.<hora HHMM> — ex.: 1.20260722.1213. Épico
# é um número bumped manualmente (marco grande); SESSIONFLOW_EPIC sobrescreve.
EPIC="${SESSIONFLOW_EPIC:-1}"
export RELEASE_VERSION="${EPIC}.$(git log -1 --format=%cd --date=format:%Y%m%d.%H%M HEAD)"
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
