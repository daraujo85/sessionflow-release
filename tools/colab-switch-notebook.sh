#!/usr/bin/env bash
# Troca qual notebook Drive o worker SF vai abrir pro próximo `sf start`.
# Útil quando você quer usar a mesma máquina T4 pra finalidade diferente
# (TTS / imagegen / avatar3d / transcribe) sem trocar de host.
#
# Uso:
#   tools/colab-switch-notebook.sh colab1-pro tts
#   tools/colab-switch-notebook.sh colab2-pro avatar3d
#   tools/colab-switch-notebook.sh colab1-pro imagegen
#   tools/colab-switch-notebook.sh colab2-pro transcribe
#   tools/colab-switch-notebook.sh colab1-pro boot       # volta pro genérico
#   tools/colab-switch-notebook.sh colab1-pro status     # mostra o atual
set -euo pipefail

HOST="$1"
PURPOSE="${2:-status}"

# URI do Mongo vem do .env do repo (nunca hardcoded).
REPO="$(cd "$(dirname "$0")/.." && pwd)"
export MONGO_URI_HOST="${MONGO_URI_HOST:-$(grep -E '^MONGO_URI_HOST=' "$REPO/.env" | cut -d= -f2-)}"

# ID dos notebooks (atualizado 2026-10-08)
case "$PURPOSE" in
  cpu)        ID="1WRlvNXyQGyYZ9xURL5gN-7ZmmdHRBi7A" ;;
  boot)       ID="1PE-N_TS72Qjt4S57Jfe92MmewTnA7s13" ;;  # canônico
  tts)        ID="1FrJDTZNVBn4zYATiJWO2EH5-S-rYwpC4" ;;
  imagegen)   ID="1rEoAIW1eGSaq3jSS8ENuNwLGLSkp8zvL" ;;
  avatar3d)   ID="1rQfTFfEyWAsZLLF64VXJsSuePJByOy94" ;;
  transcribe) ID="1sVb5XchPZDhb48WuJFd2VmH2s2x-JgvP" ;;
  status)
    uv run --no-project --with pymongo python3 -c "
import os
from pymongo import MongoClient
c = MongoClient(os.environ['MONGO_URI_HOST'])
d = c.sessionflow.worker_status.find_one({'_id':'$HOST'})
print(f'colab_notebook_id:', d.get('colab_notebook_id') if d else 'host não encontrado')
"
    exit 0
    ;;
  *) die "finalidade desconhecida: $PURPOSE. Opções: cpu|boot|tts|imagegen|avatar3d|transcribe|status" ;;
esac

# Resolver host_id (aceita nome ou ID)
HOST_ID=$(uv run --no-project --with pymongo python3 -c "
import os
from pymongo import MongoClient
c = MongoClient(os.environ['MONGO_URI_HOST'])
d = c.sessionflow.worker_status.find_one({'\$or':[{'host_id':'$HOST'},{'_id':'$HOST'}]})
if d: print(d['_id'])
else: print('NOTFOUND')
")

if [ "$HOST_ID" = "NOTFOUND" ]; then
  die "host não encontrado: $HOST"
fi

# Atualizar Mongo
uv run --no-project --with pymongo python3 -c "
import os
from pymongo import MongoClient
c = MongoClient(os.environ['MONGO_URI_HOST'])
r = c.sessionflow.worker_status.update_one({'_id':'$HOST_ID'}, {'\$set': {'colab_notebook_id':'$ID'}})
print(f'{r.matched_count} matched, {r.modified_count} modified')
"

# Reiniciar API pra carregar
docker restart sessionflow-api >/dev/null 2>&1 || true
sleep 3

echo "✓ $HOST ($HOST_ID) → $PURPOSE ($ID)"
echo "  Próximo 'sf start $HOST' vai abrir o notebook $PURPOSE"
