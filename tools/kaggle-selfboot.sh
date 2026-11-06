#!/usr/bin/env bash
# Auto-boot do worker SessionFlow dentro de um notebook Kaggle.
#
# Kaggle e Colab são o mesmo problema (VM efêmera, root, tmux/cloudflared/uv,
# agente como usuário `diego`, grafo consumido do MinIO) — a única diferença
# real é o root do sandbox: /kaggle/working em vez de /content. Por isso este
# arquivo NÃO duplica o selfboot: ele fixa o root e delega ao mesmo script.
#
# Uso (célula do notebook Kaggle):
#     !curl -fsSL <url-do-release>/tools/kaggle-selfboot.sh -o /tmp/k.sh && bash /tmp/k.sh
#
# Secrets necessários (Kaggle ▸ Add-ons ▸ Secrets, expostos como variáveis de
# ambiente quando "Attach to notebook" está ligado):
#     SF_ENV_B64          .env do worker em base64 (obrigatório)
#     SF_HOST_ID          host_id deste Kaggle em runtime CPU (obrigatório)
#     SF_HOST_ID_GPU      host_id quando o runtime tem GPU (opcional)
#     ANTHROPIC_AUTH_TOKEN                          (opcional, 9router)
#     GRAPHIFY_S3_SECRET_KEY                        (opcional, grafo do MinIO)
#
# Pegadinhas do Kaggle que o selfboot já cobre por ser o mesmo script:
#   - a internet vem DESLIGADA por padrão no notebook (ligar em Settings ▸
#     Internet, e o kernel precisa ser reiniciado depois);
#   - a sessão cai em ~12h (GPU) / ~9h (CPU);
#   - /kaggle/input é read-only; só /kaggle/working é gravável.
set -uo pipefail

export SF_SANDBOX_ROOT="${SF_SANDBOX_ROOT:-/kaggle/working}"
export SF_SANDBOX_ROOT_EXPLICITO=1   # não deixa a autodetecção sobrescrever

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALVO="$AQUI/colab-selfboot.sh"

if [ ! -f "$ALVO" ]; then
  # Rodando de /tmp (célula que baixou só este arquivo): busca o irmão no release.
  RELEASE="${SF_RELEASE_RAW:-https://raw.githubusercontent.com/daraujo85/sessionflow-release/main}"
  echo "[kaggle] baixando colab-selfboot.sh do release"
  curl -fsSL "$RELEASE/tools/colab-selfboot.sh" -o /tmp/colab-selfboot.sh || {
    echo "[kaggle] ❌ não baixei colab-selfboot.sh" >&2; exit 1; }
  ALVO=/tmp/colab-selfboot.sh
fi

echo "[kaggle] delegando ao selfboot com SF_SANDBOX_ROOT=$SF_SANDBOX_ROOT"
exec bash "$ALVO" "$@"
