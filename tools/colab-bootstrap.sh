#!/usr/bin/env bash
# Bootstrap do worker SessionFlow num runtime Google Colab ZERADO.
#
# Runtime novo do Colab perde tudo: a célula do notebook só sobe sshd + bore,
# e o worker é pulado em silêncio se /root/sessionflow/worker não existir.
# Este script, rodado no Mac, refaz o host inteiro via SSH:
#
#   tools/colab-bootstrap.sh [ssh-host] [host-id]
#
# Defaults: ssh-host=colab-worker, host-id=Colab 1 (CPU). Atualize a Port do
# ~/.ssh/config com a porta do bore impressa pelo notebook antes de rodar.
set -euo pipefail

SSH_HOST="${1:-colab-worker}"
HOST_ID="${2:-c775ad00-58bb-44ae-9781-7e7e23b5404d}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
O=(-o ConnectTimeout=20 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ServerAliveInterval=15)

echo "→ código do worker + .env + host_id"
ssh "${O[@]}" "$SSH_HOST" 'mkdir -p /root/sessionflow /root/.claude'
git -C "$REPO" archive HEAD worker tools | ssh "${O[@]}" "$SSH_HOST" 'tar -x -C /root/sessionflow'
ssh "${O[@]}" "$SSH_HOST" 'cat > /root/sessionflow/.env && chmod 600 /root/sessionflow/.env' < "$REPO/.env"
ssh "${O[@]}" "$SSH_HOST" "echo -n $HOST_ID > /root/.claude/.sessionflow-host-id"

echo "→ dependências, proxies cloudflared, CLIs e worker"
ssh "${O[@]}" "$SSH_HOST" 'bash -s' <<'EOF'
set -e
grep -q '^API_BASE_URL=' /root/sessionflow/.env || echo 'API_BASE_URL=https://api-sessionflow.boletoazap.dev.br' >> /root/sessionflow/.env
command -v tmux >/dev/null || apt-get install -y -qq tmux >/dev/null
apt-get install -y -qq acl >/dev/null 2>&1 || true
command -v cloudflared >/dev/null || { curl -fsSL -o /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x /usr/local/bin/cloudflared; }
command -v uv >/dev/null || pip install -q uv

# Mongo/Rabbit chegam por proxy: o .env do Mac aponta pra 127.0.0.1.
tmux kill-session -t cf 2>/dev/null || true
tmux new-session -d -s cf "cloudflared access tcp --hostname mongo-sessionflow.boletoazap.dev.br --url localhost:27017 & cloudflared access tcp --hostname rabbitmq-sessionflow.boletoazap.dev.br --url localhost:5672 & wait"

# CLIs de agente. claude roda como diego (não aceita root com bypass de permissão).
command -v node >/dev/null || { curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null 2>&1 && apt-get install -y -qq nodejs >/dev/null; }
[ -x /usr/local/bin/claude ] || npm i -g @anthropic-ai/claude-code @google/gemini-cli >/dev/null 2>&1
id diego >/dev/null 2>&1 || useradd -m -s /bin/bash diego
if [ ! -x /usr/local/bin/claude ]; then
  REAL=$(readlink -f "$(command -v claude)")
  printf '#!/bin/bash\nexec runuser -u diego -- env HOME=/home/diego PATH="$PATH" %s "$@"\n' "$REAL" > /usr/local/bin/claude
  chmod +x /usr/local/bin/claude
fi
touch /home/diego/.claude.json && chown diego: /home/diego/.claude.json
ln -sf /home/diego/.claude.json /root/.claude.json
mkdir -p /content/sessionflow-clones /content/work
setfacl -R -m u:diego:rwX -d -m u:diego:rwX /content/sessionflow-clones /content/work 2>/dev/null || chown -R diego: /content/work
git config --system --add safe.directory '*'

cd /root/sessionflow/worker && uv sync -q
# Qwen3-TTS pra Garagem: wrapper Python persistido no repo + pip install (idempotente).
command -v qwen-tts >/dev/null 2>&1 || pip install -q --no-deps qwen-tts >/dev/null
test -f /root/sessionflow/tools/qwen_tts_generate.py || cp /root/sessionflow/.env /tmp/.env.ponytail 2>/dev/null
ls /root/sessionflow/tools/qwen_tts_generate.py && echo '✓ qwen_tts_generate.py presente' || echo '⚠ qwen script sumiu — verifique /root/sessionflow/tools'
tmux kill-session -t sessionflow-worker 2>/dev/null || true
tmux new-session -d -s sessionflow-worker "cd /root/sessionflow/worker && while true; do uv run python -m sessionflow_worker 2>&1 | tee -a /tmp/worker.log; sleep 5; done"
sleep 15
grep -q 'Worker no ar' /tmp/worker.log && echo '✅ worker no ar' || { tail -20 /tmp/worker.log; exit 1; }
bash "$(cd "$(dirname "$0")" && pwd)/colab-9router.sh" "$SSH_HOST" || echo '⚠ 9router config falhou (Claude usa API direto)'
EOF
