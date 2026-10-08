#!/usr/bin/env bash
# Publica origin/main no mirror público (remote "public" = daraujo85/sessionflow-release)
# SEM levar o histórico privado: cria um commit com a árvore de origin/main em cima
# de public/main. Fast-forward → o self-update.sh dos amigos (merge --ff-only) segue ok.
#
# Uso: tools/publish-release.sh ["mensagem do release"]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

MSG="${1:-release: $(git log -1 --format=%s origin/main 2>/dev/null || echo update)}"

git remote get-url public >/dev/null 2>&1 \
  || git remote add public https://github.com/daraujo85/sessionflow-release.git
git fetch -q origin main
git fetch -q public main 2>/dev/null || true

TREE="$(git rev-parse 'origin/main^{tree}')"

# Guarda: nenhum valor secreto do .env pode estar na árvore publicada.
if [ -f .env ]; then
  PAT="$(mktemp)"; trap 'rm -f "$PAT"' EXIT
  grep -E '^[A-Z0-9_]*(PASS|SECRET|TOKEN|KEY|URI)[A-Z0-9_]*=' .env \
    | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' \
    | awk 'length($0) >= 8' > "$PAT" || true
  # URIs: checa só a senha embutida (user:SENHA@host), não a URI inteira.
  sed -n 's#^[a-z+]*://[^:/@]*:\([^@]*\)@.*#\1#p' "$PAT" | awk 'length($0) >= 8' >> "$PAT"
  if [ -s "$PAT" ] && git grep -q -F -f "$PAT" origin/main -- . 2>/dev/null; then
    echo "✗ origin/main contém valor secreto do .env nestes arquivos (valores não exibidos):" >&2
    git grep -l -F -f "$PAT" origin/main -- . | sed 's#^origin/main:#  #' >&2
    exit 1
  fi
fi

if git rev-parse -q --verify public/main >/dev/null; then
  if [ "$(git rev-parse 'public/main^{tree}')" = "$TREE" ]; then
    echo "✓ release já está em dia com origin/main ($(git rev-parse --short public/main))"
    exit 0
  fi
  C="$(git commit-tree "$TREE" -p public/main -m "$MSG")"
else
  C="$(git commit-tree "$TREE" -m "$MSG")"   # repo vazio: commit raiz
fi

git push public "${C}:refs/heads/main"
echo "✓ release publicado: $(git rev-parse --short "$C") → https://github.com/daraujo85/sessionflow-release"
