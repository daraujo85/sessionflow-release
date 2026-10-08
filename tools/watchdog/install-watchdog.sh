#!/usr/bin/env bash
set -e

# ==============================================================================
# Instalador do SessionFlow API Watchdog
# Suporta macOS (LaunchAgent) e Linux (systemd user timer / cron)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WATCHDOG_SRC="$SCRIPT_DIR/sessionflow-watchdog.sh"
TARGET_BIN="$HOME/.local/bin/sessionflow-watchdog.sh"

echo "=== Instalando SessionFlow API Watchdog ==="

# 1. Copia o script para ~/.local/bin
mkdir -p "$HOME/.local/bin"
cp "$WATCHDOG_SRC" "$TARGET_BIN"
chmod +x "$TARGET_BIN"
echo "✓ Script instalado em $TARGET_BIN"

# 2. Configura no macOS (LaunchAgent)
if [[ "$OSTYPE" == "darwin"* ]]; then
    PLIST_DEST="$HOME/Library/LaunchAgents/com.sessionflow.api-watchdog.plist"
    mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

    # Substitui caminho do script de forma portável com o $HOME do usuário atual
    sed "s|/Users/diegoaraujo|$HOME|g" "$SCRIPT_DIR/com.sessionflow.api-watchdog.plist" > "$PLIST_DEST"

    # Recarrega no launchctl
    launchctl unload "$PLIST_DEST" 2>/dev/null || true
    launchctl load "$PLIST_DEST"
    echo "✓ LaunchAgent registrado e ativado (roda a cada 60s):"
    launchctl list | grep "com.sessionflow.api-watchdog" || true
    echo "Logs disponíveis em: $HOME/Library/Logs/sessionflow-watchdog.log"
else
    # Linux: se tiver systemd user, configura unit/timer; senão instrui cron
    SYSTEMD_DIR="$HOME/.config/systemd/user"
    if command -v systemctl &>/dev/null; then
        mkdir -p "$SYSTEMD_DIR"
        cat <<EOF > "$SYSTEMD_DIR/sessionflow-watchdog.service"
[Unit]
Description=SessionFlow API Watchdog
After=docker.service

[Service]
Type=oneshot
ExecStart=$TARGET_BIN
EOF

        cat <<EOF > "$SYSTEMD_DIR/sessionflow-watchdog.timer"
[Unit]
Description=Run SessionFlow API Watchdog every 60s

[Timer]
OnBootSec=1min
OnUnitActiveSec=60s
AccuracySec=5s

[Install]
WantedBy=timers.target
EOF

        systemctl --user daemon-reload
        systemctl --user enable --now sessionflow-watchdog.timer
        echo "✓ Timer systemd ativado no Linux (roda a cada 60s)"
    else
        echo "✓ Para ativar no cron (a cada minuto):"
        echo "  * * * * * $TARGET_BIN"
    fi
fi

echo "=== Watchdog pronto e operacional! ==="
