#!/usr/bin/env bash
# Auto-boot do worker SessionFlow DENTRO de um runtime Google Colab (como root).
#
# Runtime novo do Colab vem zerado. A célula do notebook baixa este script do
# release público e roda — monta o host inteiro sem SSH do Mac:
#
#   SF_ENV_B64=... SF_HOST_ID=... bash colab-selfboot.sh
#
# Env (Colab Secrets; gere os valores com `tools/colab-secrets.sh` no Mac):
#   SF_ENV_B64            .env do worker em base64 (obrigatório)
#   SF_HOST_ID            host_id deste Colab em runtime CPU (obrigatório)
#   SF_HOST_ID_GPU        host_id quando o runtime tem GPU (opcional; default SF_HOST_ID)
#   ANTHROPIC_AUTH_TOKEN  token do 9router p/ o claude (opcional)
#
# Idempotente: rodar de novo atualiza o código e reinicia proxies + worker.
set -euo pipefail

: "${SF_ENV_B64:?defina o Colab Secret SF_ENV_B64}"
: "${SF_HOST_ID:?defina o Colab Secret SF_HOST_ID}"
RELEASE_URL="${SF_RELEASE_URL:-https://github.com/daraujo85/sessionflow-release.git}"
SF=/root/sessionflow

# Root do sandbox: /content no Colab, /kaggle/working no Kaggle. É o que a
# pessoa vê como pasta de trabalho e o que sobrevive entre execuções — por isso
# tudo que é montado (clone, work, clones de sessão) nasce embaixo dele. O
# default preserva o comportamento histórico do Colab.
SF_SANDBOX_ROOT="${SF_SANDBOX_ROOT:-/content}"
# Kaggle expõe KAGGLE_KERNEL_RUN_TYPE; se o root não foi informado, usa o dele.
if [ -z "${SF_SANDBOX_ROOT_EXPLICITO:-}" ] && [ -n "${KAGGLE_KERNEL_RUN_TYPE:-}" ]; then
  SF_SANDBOX_ROOT=/kaggle/working
fi
mkdir -p "$SF_SANDBOX_ROOT"

WORK="$SF_SANDBOX_ROOT/sessionflow"   # clone de trabalho → `--dir sessionflow` acha aqui
log() { echo "[selfboot] $*"; }

HOST_ID="$SF_HOST_ID"
if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1 && [ -n "${SF_HOST_ID_GPU:-}" ]; then
  HOST_ID="$SF_HOST_ID_GPU"
fi

log "pacotes base"
export DEBIAN_FRONTEND=noninteractive
command -v tmux >/dev/null && command -v setfacl >/dev/null && command -v rsync >/dev/null \
  || { apt-get update -qq >/dev/null; apt-get install -y -qq tmux acl rsync >/dev/null; }
command -v cloudflared >/dev/null || {
  curl -fsSL -o /usr/local/bin/cloudflared \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x /usr/local/bin/cloudflared
}
command -v uv >/dev/null || pip install -q uv

log "código do release público"
if [ -d "$WORK/.git" ]; then
  git -C "$WORK" fetch -q --depth 1 origin main && git -C "$WORK" reset -q --hard FETCH_HEAD
else
  git clone -q --depth 1 "$RELEASE_URL" "$WORK"
fi
mkdir -p "$SF"
rsync -a --delete --exclude .venv --exclude __pycache__ "$WORK/worker/" "$SF/worker/"
rsync -a --delete --exclude __pycache__ "$WORK/tools/" "$SF/tools/"

log ".env + host_id"
mkdir -p /root/.claude
(umask 077; printf '%s' "$SF_ENV_B64" | base64 -d > "$SF/.env")
grep -q '^API_BASE_URL=' "$SF/.env" || echo 'API_BASE_URL=https://api-sessionflow.boletoazap.dev.br' >> "$SF/.env"
grep -q '^SESSIONFLOW_SCAN_ROOTS=' "$SF/.env" || echo "SESSIONFLOW_SCAN_ROOTS=$SF_SANDBOX_ROOT" >> "$SF/.env"
printf '%s' "$HOST_ID" > /root/.claude/.sessionflow-host-id

log "proxies cloudflared (mongo/rabbit em 127.0.0.1, igual ao .env do Mac)"
tmux kill-session -t cf 2>/dev/null || true
tmux new-session -d -s cf "cloudflared access tcp --hostname mongo-sessionflow.boletoazap.dev.br --url localhost:27017 & cloudflared access tcp --hostname rabbitmq-sessionflow.boletoazap.dev.br --url localhost:5672 & wait"

log "CLIs de agente"
command -v node >/dev/null || {
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null 2>&1
  apt-get install -y -qq nodejs >/dev/null
}
[ -e /usr/local/bin/claude.real ] || command -v claude >/dev/null \
  || npm i -g @anthropic-ai/claude-code @google/gemini-cli >/dev/null 2>&1
# claude roda como diego: root não aceita bypass de permissão (ver colab-9router.sh).
id diego >/dev/null 2>&1 || useradd -m -s /bin/bash diego
[ -e /usr/local/bin/claude.real ] || mv "$(readlink -f "$(command -v claude)")" /usr/local/bin/claude.real
P=/etc/profile.d/9router.sh
if [ -n "${ANTHROPIC_AUTH_TOKEN:-}" ]; then
  (umask 077
   {
     echo 'export ANTHROPIC_BASE_URL="https://9router-mesh-sessionflow.boletoazap.dev.br"'
     printf 'export ANTHROPIC_AUTH_TOKEN=%q\n' "$ANTHROPIC_AUTH_TOKEN"
     echo 'export ANTHROPIC_MODEL="${ANTHROPIC_MODEL:-claude-haiku-4-5-20251001}"'
     echo 'export ANTHROPIC_SMALL_FAST_MODEL="${ANTHROPIC_SMALL_FAST_MODEL:-claude-haiku-4-5-20251001}"'
     echo 'export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1'
     echo 'export DISABLE_TELEMETRY=1'
   } > "$P")
  chmod 644 "$P"
fi
touch "$P"
# setpriv (não runuser): runuser deixa um pai PAM com cwd=/ em foreground no
# pane → tmux reporta pane_current_path=/ e o work_dir da sessão vira "/".
printf '#!/bin/bash\n. %s\nexec setpriv --reuid diego --regid diego --init-groups env HOME=/home/diego PATH="$PATH" /usr/local/bin/claude.real "$@"\n' "$P" \
  > /usr/local/bin/claude
chmod +x /usr/local/bin/claude
# `sf` pro agente (roda como diego): handoff/share/send de volta pro chefe.
install -m 640 -g diego "$SF/.env" /home/diego/.sessionflow.env
printf '#!/bin/bash\nSESSIONFLOW_ENV_PATH=/home/diego/.sessionflow.env exec python3 /usr/local/lib/sf "$@"\n' > /usr/local/bin/sf
install -m 755 "$SF/tools/sf" /usr/local/lib/sf
chmod +x /usr/local/bin/sf
# JSON válido (arquivo vazio = "configuration error" e o claude trava no prompt);
# onboarding/bypass já aceitos pra sessão subir sem diálogo.
CJ=/home/diego/.claude.json
[ -s "$CJ" ] || echo '{"hasCompletedOnboarding":true,"bypassPermissionsModeAccepted":true,"projects":{}}' > "$CJ"
chown diego: "$CJ"
ln -sf /home/diego/.claude.json /root/.claude.json
mkdir -p "$SF_SANDBOX_ROOT/sessionflow-clones" "$SF_SANDBOX_ROOT/work"
setfacl -R -m u:diego:rwX -d -m u:diego:rwX "$SF_SANDBOX_ROOT/sessionflow-clones" "$SF_SANDBOX_ROOT/work" "$WORK" 2>/dev/null \
  || chown -R diego: "$SF_SANDBOX_ROOT/sessionflow-clones" "$SF_SANDBOX_ROOT/work" "$WORK"
git config --system --add safe.directory '*'

log "worker"
cd "$SF/worker" && uv sync -q

# Servidor MCP do agente. Duas armadilhas que só existem no Colab:
#  1. /root é drwx------ → um venv ali fica registrado no .claude.json mas o
#     agente (usuário diego) NÃO consegue executar; o sintoma é "as tools do
#     MCP simplesmente não aparecem", sem erro visível.
#  2. a VM é efêmera → sem reinstalar a cada boot, o runtime novo volta sem MCP.
# Por isso os tools vão para /opt/sessionflow-mcp (mundo-legível) e o installer
# roda com o HOME do diego, para registrar no .claude.json DELE com caminho
# que ele abre. O install-mcp.sh resolve REPO a partir da própria localização,
# então copiando tools/ para lá o venv nasce em /opt/sessionflow-mcp/tools/.
log "Servidor MCP do agente"
MCP_HOME=/opt/sessionflow-mcp
mkdir -p "$MCP_HOME/tools"
install -m 755 "$SF/tools/mcp_server.py" "$MCP_HOME/tools/mcp_server.py"
install -m 755 "$SF/tools/install-mcp.sh" "$MCP_HOME/tools/install-mcp.sh"
install -m 755 "$SF/tools/sf" "$MCP_HOME/tools/sf"
install -m 644 "$SF/tools/mcp-requirements.txt" "$MCP_HOME/tools/mcp-requirements.txt"
if HOME=/home/diego PATH="/usr/local/bin:$PATH" "$MCP_HOME/tools/install-mcp.sh" >/tmp/mcp-install.log 2>&1; then
  chown -R diego: "$MCP_HOME"
  chmod -R a+rX "$MCP_HOME"
  log "✅ MCP registrado para o agente (diego) em $MCP_HOME"
else
  log "⚠ install-mcp.sh falhou — o agente sobe sem MCP (log em /tmp/mcp-install.log)"
  tail -5 /tmp/mcp-install.log 2>/dev/null || true
fi

# Grafo do graphify: o Colab CONSOME o artefato gerado no Mac (~7 MB) em vez de
# rodar `graphify extract` — a VM é efêmera e reindexar a cada boot é desperdício.
# O Colab é host de execução, não de indexação: aqui NUNCA se roda `graphify
# update .` (degrada o grafo: a árvore do Colab é menor e não tem histórico).
# Não-fatal de propósito: MinIO fora do ar ou token ausente não pode impedir o
# worker de subir. Por isso todo o bloco vive dentro de um `if`.
if [ -n "${GRAPHIFY_S3_SECRET_KEY:-}" ]; then
  log "grafo do graphify (MinIO)"
  SF_GRAPH="$WORK"   # o repo que o agente vê como --dir sessionflow
  SEGREDOS="${HOME:-/root}/.claude/secrets"
  if ( umask 077
       mkdir -p "$SEGREDOS"
       cat > "$SEGREDOS/graphify-s3.env" <<EOF
GRAPHIFY_S3_ENDPOINT=${GRAPHIFY_S3_ENDPOINT:-https://s3.boletoazap.dev.br}
GRAPHIFY_S3_ACCESS_KEY=${GRAPHIFY_S3_ACCESS_KEY:-graphify}
GRAPHIFY_S3_SECRET_KEY=${GRAPHIFY_S3_SECRET_KEY}
GRAPHIFY_S3_BUCKET=${GRAPHIFY_S3_BUCKET:-graphify}
EOF
     ) \
     && "$WORK/tools/graph-fetch.py" --dir "$SF_GRAPH" fetch >/tmp/graph-fetch.log 2>&1; then
    chown -R diego: "$SF_GRAPH/graphify-out" 2>/dev/null || true
    chmod -R a+rX "$SF_GRAPH/graphify-out" 2>/dev/null || true
    log "✅ grafo baixado ($(grep -c '↓' /tmp/graph-fetch.log 2>/dev/null || echo 0) objetos)"
  else
    log "⚠ grafo não baixado — segue sem (log em /tmp/graph-fetch.log)"
    tail -3 /tmp/graph-fetch.log 2>/dev/null || true
  fi
else
  log "grafo: secret GRAPHIFY_S3_SECRET_KEY ausente — pulando (worker sobe sem)"
fi

: > /tmp/worker.log
tmux kill-session -t sessionflow-worker 2>/dev/null || true
tmux new-session -d -s sessionflow-worker \
  "cd $SF/worker && while true; do uv run python -m sessionflow_worker 2>&1 | tee -a /tmp/worker.log; sleep 5; done"
for _ in $(seq 1 30); do
  grep -q 'Worker no ar' /tmp/worker.log && { log "✅ worker no ar (host_id $HOST_ID)"; exit 0; }
  sleep 2
done
tail -30 /tmp/worker.log
log "❌ worker não subiu em 60s"
exit 1
