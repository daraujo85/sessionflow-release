#!/usr/bin/env bash
# Liga um Colab sem mão humana: POST /workers/{id}/start → abre o notebook
# selfboot (colab_notebook_id/colab_authuser do worker_status) → Executar tudo
# → confirma diálogos → espera o heartbeat (🟢 em `sf hosts`).
# Se o Colab pedir humano (reCAPTCHA "Você ainda está aí?", "Sessões em
# excesso", "Fazer login"), manda web push ("🤖 Colab precisa de você") via
# `sf notify`. No captcha, deixa a aba aberta e segue esperando o heartbeat
# até SF_COLAB_ONLINE_TIMEOUT (o humano resolve → boot continua); nos demais, sai.
#
# Uso:
#   tools/colab-autostart.sh <host>              # Chrome do dia a dia (visível) — validação
#   tools/colab-autostart.sh <host> --headless   # produção: Chrome dedicado headless
#   tools/colab-autostart.sh --login             # 1x: loga as contas Google no perfil dedicado
#
# <host> = qualquer coisa que o `sf start` aceite (colab1-cpu, host_id, nome...).
# Perfil dedicado: SF_COLAB_CHROME_PROFILE (default ~/.sessionflow/colab-chrome),
# porta SF_COLAB_CDP_PORT (default 9334). Cookies do perfil = sessão Google;
# nunca versionar nem copiar pra fora da máquina.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SF="$REPO/tools/sf"
PROFILE="${SF_COLAB_CHROME_PROFILE:-$HOME/.sessionflow/colab-chrome}"
PORT="${SF_COLAB_CDP_PORT:-9334}"
CHROME="${SF_CHROME_BIN:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"
TIMEOUT="${SF_COLAB_ONLINE_TIMEOUT:-900}"

die() { echo "colab-autostart: $*" >&2; exit 1; }

start_dedicated_chrome() {  # $1 = headless|headed
  curl -fs "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1 && return 0
  mkdir -p "$PROFILE"
  local extra=()
  [ "$1" = headless ] && extra=(--headless=new --window-size=1470,900)
  "$CHROME" --remote-debugging-port="$PORT" --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check "${extra[@]}" about:blank \
    >/dev/null 2>&1 &
  for _ in $(seq 30); do
    curl -fs "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1 && return 0
    sleep 1
  done
  die "Chrome dedicado não abriu a porta $PORT"
}

if [ "${1:-}" = "--login" ]; then
  start_dedicated_chrome headed
  BU_CDP_URL="http://127.0.0.1:$PORT" BU_NAME=colab browser-harness <<'PY'
new_tab("https://accounts.google.com/AddSession")
PY
  echo "Logue as DUAS contas (authuser=0 e 1) na janela aberta, depois feche o Chrome."
  exit 0
fi

HOST="${1:?uso: colab-autostart.sh <host> [--headless] | --login}"
MODE="${2:-}"

read -r HOST_ID COLAB_URL < <(python3 - "$SF" "$HOST" <<'PY'
import importlib.machinery, importlib.util, sys
loader = importlib.machinery.SourceFileLoader("sf", sys.argv[1])
spec = importlib.util.spec_from_loader("sf", loader)
sf = importlib.util.module_from_spec(spec); loader.exec_module(sf)
env = sf.resolve_env(type("A", (), {"remote": None})())
token = sf.login(env); base = sf.api_base(env)
host = sf.resolve_host(sf.fetch_workers(base, token), sys.argv[2], allow_offline=True)
if not host:
    sys.exit(f"host '{sys.argv[2]}' não encontrado")
_, body = sf._request("POST", f"{base}/workers/{host['host_id']}/start", token=token)
url = (body or {}).get("colab_url")
if not url:
    sys.exit("host não é Colab (sem colab_url)")
print(host["host_id"], url)
PY
) || die "falha ao resolver host/URL"

echo "▶️  $HOST ($HOST_ID) → $COLAB_URL"
if [ "$MODE" = "--headless" ]; then
  start_dedicated_chrome headless
  export BU_CDP_URL="http://127.0.0.1:$PORT" BU_NAME=colab
fi
BH="$REPO/tools/colab_autostart_bh.py"
out=$(SF_COLAB_URL="$COLAB_URL" browser-harness < "$BH" 2>&1) && rc=0 || rc=$?
[ -n "$out" ] && echo "$out"
if [ "$rc" -ne 0 ]; then
  reason=$(printf '%s\n' "$out" | grep -o 'FAIL .*' | tail -1 | sed 's/^FAIL //')
  [ -n "$reason" ] || die "browser-harness falhou (rc=$rc)"
  # Precisa de humano: avisa no celular (best-effort — sem push, segue igual).
  "$SF" notify "🤖 Colab precisa de você" "$HOST: $reason — toque pra abrir" \
    --url "$COLAB_URL" || echo "colab-autostart: aviso push falhou" >&2
  case "$reason" in
    *reCAPTCHA*)
      # NÃO resolve o captcha: deixa o Chrome na aba do notebook pro humano e
      # um harness em modo "resume" só confirma os diálogos depois que ele sair.
      echo "🙋 captcha na aba do notebook — resolva lá; aguardando o boot..."
      SF_COLAB_URL="$COLAB_URL" SF_COLAB_RESUME=1 SF_COLAB_ONLINE_TIMEOUT="$TIMEOUT" \
        browser-harness < "$BH" &
      RESUME_PID=$!
      trap 'kill "$RESUME_PID" 2>/dev/null || true' EXIT
      ;;
    *) die "$reason" ;;
  esac
fi

echo "⏳ aguardando heartbeat (até ${TIMEOUT}s)..."
end=$(( $(date +%s) + TIMEOUT ))
until "$SF" hosts 2>/dev/null | grep -q "$HOST_ID online"; do
  [ "$(date +%s)" -lt "$end" ] || die "não ficou online em ${TIMEOUT}s (ver output da célula no notebook)"
  sleep 20
done
echo "🟢 $HOST online"
