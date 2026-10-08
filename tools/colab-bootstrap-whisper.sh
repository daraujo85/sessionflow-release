#!/usr/bin/env bash
# Bootstrap pro Colab T4 (transcribe-audio-video GPU).
# Mesmo esqueleto de colab-bootstrap.sh + instala Whisper + ffmpeg + Qwen2-VL 2B
# pra rodar a skill transcribe-audio-video com GPU (Whisper+vision em paralelo).
#
#   tools/colab-bootstrap-whisper.sh [ssh-host] [host-id]
#
# Defaults: ssh-host=colab2-pro, host-id=Colab 2 (T4). Atualize a Port do
# ~/.ssh/config com a porta do bore impressa pelo notebook antes de rodar.
set -euo pipefail

SSH_HOST="${1:-colab2-pro}"
HOST_ID="${2:-02151543-4ff8-44c2-b607-b1dfc8625067}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
O=(-o ConnectTimeout=20 -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ServerAliveInterval=15)

echo "→ código do worker + .env + host_id"
ssh "${O[@]}" "$SSH_HOST" 'mkdir -p /root/sessionflow /root/.claude'
git -C "$REPO" archive HEAD worker tools | ssh "${O[@]}" "$SSH_HOST" 'tar -x -C /root/sessionflow'
ssh "${O[@]}" "$SSH_HOST" 'cat > /root/sessionflow/.env && chmod 600 /root/sessionflow/.env' < "$REPO/.env"
ssh "${O[@]}" "$SSH_HOST" "echo -n $HOST_ID > /root/.claude/.sessionflow-host-id"

echo "→ dependências, proxies, CLIs e worker"
ssh "${O[@]}" "$SSH_HOST" 'bash -s' <<'EOF'
set -e
grep -q '^API_BASE_URL=' /root/sessionflow/.env || echo 'API_BASE_URL=https://api-sessionflow.boletoazap.dev.br' >> /root/sessionflow/.env
command -v tmux >/dev/null || apt-get install -y -qq tmux >/dev/null
apt-get install -y -qq acl ffmpeg >/dev/null 2>&1 || true
command -v cloudflared >/dev/null || { curl -fsSL -o /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 && chmod +x /usr/local/bin/cloudflared; }
command -v uv >/dev/null || pip install -q uv

# Mongo/Rabbit chegam por proxy: o .env do Mac aponta pra 127.0.0.1.
tmux kill-session -t cf 2>/dev/null || true
tmux new-session -d -s cf "cloudflared access tcp --hostname mongo-sessionflow.boletoazap.dev.br --url localhost:27017 & cloudflared access tcp --hostname rabbitmq-sessionflow.boletoazap.dev.br --url localhost:5672 & wait"

# CLIs de agente. claude roda como diego.
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

# === Transcribe-audio-video GPU stack ===
echo "→ instalando Whisper + Qwen2-VL + ffmpeg-python (transcribe-audio-video GPU)"
pip install -q --no-deps openai-whisper ffmpeg-python av 2>&1 | tail -3
pip install -q transformers torch torchvision --upgrade 2>&1 | tail -3
pip install -q qwen-vl-utils accelerate 2>&1 | tail -3
# Whisper large-v3 baixa ~3GB no 1º uso. Qwen2-VL 2B = ~4GB. Cache em ~/.cache/huggingface.
mkdir -p /root/.cache/huggingface
ls /root/sessionflow/tools/vision_analyze_frames.py && echo '✓ vision_analyze_frames.py presente' || echo '⚠ vision script sumiu'
# Re-usa a config 9router (wrapper claude + env vars pro gateway local do Diego)
bash "$(cd "$(dirname "$0")" && pwd)/colab-9router.sh" "$(echo $SSH_HOST)" || echo '⚠ 9router config falhou'

# symlink: o worker SF enxerga tools/colab-bootstrap-whisper.sh e tools/vision_analyze_frames.py
# nas tools/ do sessionflow, que já foram copiadas no tar inicial.

tmux kill-session -t sessionflow-worker 2>/dev/null || true
tmux new-session -d -s sessionflow-worker "cd /root/sessionflow/worker && while true; do uv run python -m sessionflow_worker 2>&1 | tee -a /tmp/worker.log; sleep 5; done"
sleep 15
grep -q 'Worker no ar' /tmp/worker.log && echo '✅ worker no ar' || { tail -20 /tmp/worker.log; exit 1; }
echo '✅ bootstrap transcribe OK — Whisper + Qwen2-VL 2B + ffmpeg prontos na T4'
EOF

echo "→ atualizando ~/.ssh/config: porta bore → $SSH_HOST"
# Caller responsibility: a porta do bore é impressa na célula do notebook.
# Esta função só mostra qual é a porta atual e qual deveria ser:
ssh -o ConnectTimeout=3 "${O[@]}" "$SSH_HOST" 'echo connected' 2>&1 | head -3 || echo "⚠ ssh falhou: ajuste a Port em ~/.ssh/config com a porta do bore atual"
