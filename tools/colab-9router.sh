#!/usr/bin/env bash
# Aplica a config 9router no Colab (idempotente).
# Chamado pelo colab-bootstrap.sh no fim. Pode ser re-rodado a qualquer momento.
set -euo pipefail
SSH="${1:-colab-worker}"
# Token do 9router: só do ambiente (nunca no repo). Ex.: export ANTHROPIC_AUTH_TOKEN=...
: "${ANTHROPIC_AUTH_TOKEN:?defina ANTHROPIC_AUTH_TOKEN (token do 9router) antes de rodar}"

echo "→ cria /etc/profile.d/9router.sh no Colab"
# Token vai no stdin logo após o `read` (fora da linha de comando/ps do Colab).
{ echo 'read -r TOKEN'; printf '%s\n' "$ANTHROPIC_AUTH_TOKEN"; cat <<'REMOTE'; } | ssh "$SSH" 'bash -s'
set -e
cat > /etc/profile.d/9router.sh <<'EOF'
# 9router via cloudflare-tunnel "macbook" → Claude Code no Colab.
# Token em ~/.claude/secrets/cloudflare-boletoazap.env (CF_BOLETOAZAP_API_TOKEN);
# o host.docker.internal do tunnel macbook resolve para o Mac onde o 9router escuta em :20128.
export ANTHROPIC_BASE_URL="https://9router-mesh-sessionflow.boletoazap.dev.br"
export ANTHROPIC_AUTH_TOKEN="__ANTHROPIC_AUTH_TOKEN__"
export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-haiku-4-5-20251001}"
export ANTHROPIC_SMALL_FAST_MODEL="${ANTHROPIC_SMALL_FAST_MODEL:-claude-haiku-4-5-20251001}"
export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT=1
export DISABLE_TELEMETRY=1
EOF
sed -i "s|__ANTHROPIC_AUTH_TOKEN__|$TOKEN|" /etc/profile.d/9router.sh
chmod 644 /etc/profile.d/9router.sh

# Wrapper do claude — preserva e encaminha env ao binário real sob runuser.
if [ ! -e /usr/local/bin/claude.real ]; then
  mv "$(readlink -f "$(command -v claude)")" /usr/local/bin/claude.real 2>/dev/null || true
fi
cat > /usr/local/bin/claude <<'WRAP'
#!/bin/bash
. /etc/profile.d/9router.sh
exec runuser -u diego -- env HOME=/home/diego PATH="$PATH" /usr/local/bin/claude.real "$@"
WRAP
chmod +x /usr/local/bin/claude

# Limpa .claude.json corrompido se aparecer (claude auto-recria).
rm -f /home/diego/.claude.json /home/diego/.claude.json.backup
REMOTE
echo "✓ 9router config aplicado em $SSH"
