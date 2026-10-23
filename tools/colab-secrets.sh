#!/usr/bin/env bash
# Gera os valores dos Colab Secrets usados pelo tools/colab-selfboot.sh.
# Nunca imprime segredo no terminal: copia um por vez pro clipboard (pbcopy).
#
#   tools/colab-secrets.sh                     # lista o que existe (só nomes/tamanhos)
#   tools/colab-secrets.sh SF_ENV_B64          # copia o .env mínimo em base64
#   tools/colab-secrets.sh ANTHROPIC_AUTH_TOKEN
#
# SF_HOST_ID / SF_HOST_ID_GPU não são segredo: veja `sf hosts` (Colab 1 CPU =
# c775ad00-...-404d, Colab 1 Pro = ...-404e, Colab 2 CPU = 02151543-...-5066, Pro = ...-5067).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"

# Só o que o worker do Colab precisa — nada de credencial root/JWT/VAPID do servidor.
KEYS='^(MONGO_DB|MONGO_URI_HOST|RABBITMQ_VHOST|RABBITMQ_URI_HOST|SESSIONFLOW_EMAIL|SESSIONFLOW_PASSWORD|API_BASE_URL|SESSIONFLOW_JARVIS_[A-Z_]+)='

env_b64() { grep -E "$KEYS" "$REPO/.env" | base64 | tr -d '\n'; }

case "${1:-}" in
  SF_ENV_B64) env_b64 | pbcopy; echo "✓ SF_ENV_B64 copiado ($(env_b64 | wc -c | tr -d ' ') chars)" ;;
  ANTHROPIC_AUTH_TOKEN)
    : "${ANTHROPIC_AUTH_TOKEN:?ANTHROPIC_AUTH_TOKEN não está no ambiente}"
    printf '%s' "$ANTHROPIC_AUTH_TOKEN" | pbcopy; echo "✓ ANTHROPIC_AUTH_TOKEN copiado" ;;
  "")
    echo "SF_ENV_B64: $(grep -cE "$KEYS" "$REPO/.env") chaves ($(grep -E "$KEYS" "$REPO/.env" | cut -d= -f1 | tr '\n' ' '))"
    echo "ANTHROPIC_AUTH_TOKEN: $([ -n "${ANTHROPIC_AUTH_TOKEN:-}" ] && echo presente || echo ausente)" ;;
  *) echo "uso: $0 [SF_ENV_B64|ANTHROPIC_AUTH_TOKEN]" >&2; exit 2 ;;
esac
