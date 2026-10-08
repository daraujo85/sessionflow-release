#!/usr/bin/env bash
# ==============================================================================
# SessionFlow API Watchdog
# Verifica a saúde do backend na porta 8000. Se travar/falhar 2 vezes seguidas,
# reinicia o container automaticamente para auto-recuperação.
# ==============================================================================

PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin"
DOCKER_BIN="$(command -v docker || echo "/usr/local/bin/docker")"
HEALTH_URL="http://127.0.0.1:8000/health"
FAIL_FILE="/tmp/sessionflow-api-health.fail"
LOG_FILE="$HOME/Library/Logs/sessionflow-watchdog.log"
[ -d "$HOME/Library/Logs" ] || LOG_FILE="/tmp/sessionflow-watchdog.log"

# Se o container nem estiver rodando no Docker, não faz nada
if ! "$DOCKER_BIN" ps --format '{{.Names}}' 2>/dev/null | grep -q '^sessionflow-api$'; then
    exit 0
fi

# Testa o endpoint com timeout de 15 segundos (evita falso positivo durante tráfego intenso)
if curl -fsS -m 15 "$HEALTH_URL" >/dev/null 2>&1; then
    # Saudável: limpa qualquer falha acumulada
    rm -f "$FAIL_FILE"
    exit 0
fi

# Se falhou, incrementa contador
FAILS=0
[ -f "$FAIL_FILE" ] && FAILS=$(cat "$FAIL_FILE" 2>/dev/null || echo 0)
FAILS=$((FAILS + 1))
echo "$FAILS" > "$FAIL_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️ SessionFlow API não respondeu ao healthcheck (falha $FAILS/4)" >> "$LOG_FILE"

# Se acumulou 4 falhas consecutivas (~4 minutos), reinicia o container
if [ "$FAILS" -ge 4 ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 🚨 Reiniciando container sessionflow-api por falta de resposta persistente (4 falhas consecutivas)..." >> "$LOG_FILE"
    "$DOCKER_BIN" restart sessionflow-api >> "$LOG_FILE" 2>&1
    rm -f "$FAIL_FILE"
fi
