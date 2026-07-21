#!/usr/bin/env bash
# Roda tools/self-update.sh em loop (default 30min). Pensado pra viver numa
# sessão tmux de infra dedicada (ver README) — SEMPRE crie essa sessão com o
# nome "sessionflow-autoupdate" (o app já sabe escondê-la da tela de Sessões,
# igual sessionflow-worker/cloudflared-tunnel):
#
#   tmux new-session -d -s sessionflow-autoupdate './tools/self-update-loop.sh'
#
# Intervalo configurável via SESSIONFLOW_UPDATE_INTERVAL_SECONDS (segundos).
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

INTERVAL="${SESSIONFLOW_UPDATE_INTERVAL_SECONDS:-1800}"

while true; do
  ./tools/self-update.sh || echo "[$(date '+%Y-%m-%d %H:%M:%S')] self-update.sh falhou (tentando de novo no próximo ciclo)"
  sleep "$INTERVAL"
done
