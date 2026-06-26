"""Links compartilháveis de UMA sessão (token efêmero, escopado, revogável).

Modelo: um token aleatório fica gravado no doc da sessão (``share_token`` +
``share_expires_at``). O link tem a forma ``{origin}/s/{session_id}?k={token}``.
O convidado que abre o link só consegue agir naquela ``session_id`` — o
middleware de auth (``app.main``) aceita o token APENAS nas rotas daquela sessão
(e no SSE filtrado por ela), nunca no resto da API.

O link "morre" sozinho em três casos, checados a CADA request (não é só um
prazo): (1) a sessão foi PARADA/encerrada (status morto), (2) a sessão foi
APAGADA (doc some), (3) passou da validade (``share_expires_at``). Além disso o
dono pode REVOGAR na hora (limpa os campos) — e regerar sobrescreve o token,
invalidando o link antigo.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

# Status em que a sessão é considerada MORTA → o link para de funcionar.
# Tudo o mais (running, waiting_*, detached, etc.) mantém o link vivo.
DEAD_STATUSES = frozenset({"stopped", "completed", "error"})

# Caminho do segmento de gestão do link — o CONVIDADO nunca pode gerar/revogar.
SHARE_SUBPATH = "share"


def new_token() -> str:
    """Gera um token de share aleatório (urlsafe, ~32 chars)."""
    return secrets.token_urlsafe(24)


def is_alive(doc: dict[str, Any]) -> bool:
    """A sessão está viva o suficiente para o link valer?"""
    return str(doc.get("status") or "").lower() not in DEAD_STATUSES


def _expired(doc: dict[str, Any], now: datetime) -> bool:
    exp = doc.get("share_expires_at")
    if exp is None:
        return True  # sem validade gravada = sem link ativo
    if isinstance(exp, str):
        try:
            exp = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        except ValueError:
            return True
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    return now >= exp


def token_valid(doc: dict[str, Any] | None, token: str | None) -> bool:
    """O ``token`` casa com o link ativo da sessão E a sessão está viva/no prazo?

    Comparação em tempo constante (``compare_digest``). ``None``/vazio = inválido.
    """
    if not token or doc is None:
        return False
    stored = doc.get("share_token")
    if not stored:
        return False
    if not secrets.compare_digest(str(stored), token):
        return False
    now = datetime.now(UTC)
    if _expired(doc, now):
        return False
    return is_alive(doc)


def session_id_from_path(path: str) -> str | None:
    """Extrai o ``session_id`` de um path ``/sessions/{id}[/...]``; senão ``None``."""
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "sessions":
        return parts[1]
    return None


def path_allows_share(path: str, session_id: str) -> bool:
    """O share token pode agir neste path? (tudo da sessão, menos gerir o link.)

    Libera ``/sessions/{id}`` e ``/sessions/{id}/<ação>`` — exceto o subpath de
    gestão do próprio link (``/share``), que é só do dono.
    """
    base = f"/sessions/{session_id}"
    if path != base and not path.startswith(base + "/"):
        return False
    return not path.rstrip("/").endswith("/" + SHARE_SUBPATH)
