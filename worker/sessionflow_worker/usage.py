"""Raspagem do ``/usage`` do Claude — % REAL dos limites (sessão 5h + semanal).

O Claude Code expõe, via comando de barra ``/usage`` da TUI, o **percentual real
de consumo** dos limites do plano (que é informação *server-side*, não derivável
dos JSONL locais): a janela rolante da sessão (5h) e a janela semanal. Este
módulo sobe uma sessão tmux efêmera, roda ``claude``, dispara ``/usage`` (que
**não** manda prompt — é quota-light, só abre um painel de status), captura o
pane, limpa o ANSI e parseia.

Formato REAL capturado (já sem ANSI)::

    Current session
    ████████████████                                   32% used
    Resets 12:30pm (America/Sao_Paulo)
    Current week (all models)
    █                                                  2% used
    Resets Jun 24 at 9am (America/Sao_Paulo)
    Current week (Sonnet only)
                                                       0% used
    Resets Jun 24 at 9am (America/Sao_Paulo)

Estratégia de parse: acha o cabeçalho "Current session", pega a PRÓXIMA linha com
``N% used`` (→ ``session_pct``) e a linha "Resets ..." seguinte (→
``session_reset``); idem para "Current week (all models)" (→ ``week_pct`` /
``week_reset``). O bloco "(Sonnet only)" é opcionalmente exposto em
``week_sonnet_pct``.

Salvaguarda das sessões tmux
----------------------------
⚠️ O *scraping* cria sessões efêmeras com prefixo ``sfusage-`` e **SEMPRE** as
mata no ``finally``, com um ``assert`` de prefixo antes de matar — nenhuma outra
sessão (real do usuário ou de outro scraper) é jamais tocada. Reusa o mesmo
padrão de ``model_discovery.py``.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path

import libtmux
from motor.motor_asyncio import AsyncIOMotorDatabase

from sessionflow_worker.model_discovery import strip_ansi

logger = logging.getLogger("sessionflow_worker.usage")

# Coleção (doc ÚNICO) com o último snapshot dos limites do host.
HOST_USAGE_COLLECTION = "host_usage"
# Chave fixa do doc único (sempre upsert no mesmo registro).
HOST_USAGE_KEY = "host"

# Prefixo OBRIGATÓRIO das sessões efêmeras de scraping (cinto de segurança).
SCRAPE_PREFIX = "sfusage-"

# Timings do scraping (segundos). A TUI do claude leva ~10-12s para aceitar slash
# commands; abaixo disso o ``/usage`` é descartado. O painel renderiza rápido.
_BOOT_WAIT = 11.0
_USAGE_WAIT = 5.0

# Cabeçalhos dos blocos no painel do /usage.
_SESSION_HEADER = "current session"
_WEEK_ALL_HEADER = "current week (all models)"
_WEEK_SONNET_HEADER = "current week (sonnet only)"

# Linha "N% used" (com a barra de progresso opcional à esquerda).
_PCT_RE = re.compile(r"(\d+)\s*%\s*used", re.IGNORECASE)
# Linha "Resets <quando> (<tz>)".
_RESET_RE = re.compile(r"Resets\s+(.+)$", re.IGNORECASE)


def _parse_block(lines: list[str], start: int) -> tuple[int | None, str | None]:
    """A partir de ``lines[start]`` (um cabeçalho), extrai ``(pct, reset)``.

    Varre as linhas seguintes ATÉ o próximo cabeçalho conhecido (ou o fim):
    a primeira linha com ``N% used`` vira o pct e a primeira ``Resets ...``
    vira o reset. Robusto à linha de barra vazia (0%) onde a barra some.
    """
    pct: int | None = None
    reset: str | None = None
    headers = (_SESSION_HEADER, _WEEK_ALL_HEADER, _WEEK_SONNET_HEADER)
    for line in lines[start + 1 :]:
        low = line.strip().lower()
        if low in headers:
            break
        if pct is None:
            m = _PCT_RE.search(line)
            if m:
                pct = int(m.group(1))
        if reset is None:
            m = _RESET_RE.search(line)
            if m:
                reset = m.group(1).strip()
        if pct is not None and reset is not None:
            break
    return pct, reset


def parse_usage(text: str) -> dict | None:
    """Parseia o painel ``/usage`` (ANSI já removido) em um dict de limites.

    Retorna ``{session_pct, session_reset, week_pct, week_reset,
    week_sonnet_pct}`` ou ``None`` se o painel não contiver o bloco da sessão
    (texto vazio / tela errada / boot incompleto). Os campos ``week_*`` ficam
    ``None`` quando o respectivo bloco não aparece; ``week_sonnet_pct`` é
    opcional (``None`` quando ausente).
    """
    lines = text.splitlines()
    lowered = [ln.strip().lower() for ln in lines]

    def _find(header: str) -> int | None:
        for i, low in enumerate(lowered):
            if low == header:
                return i
        return None

    session_idx = _find(_SESSION_HEADER)
    if session_idx is None:
        return None

    session_pct, session_reset = _parse_block(lines, session_idx)
    # Sem o pct da sessão o painel não foi realmente renderizado.
    if session_pct is None:
        return None

    week_idx = _find(_WEEK_ALL_HEADER)
    week_pct, week_reset = (
        _parse_block(lines, week_idx) if week_idx is not None else (None, None)
    )

    sonnet_idx = _find(_WEEK_SONNET_HEADER)
    week_sonnet_pct = (
        _parse_block(lines, sonnet_idx)[0] if sonnet_idx is not None else None
    )

    return {
        "session_pct": session_pct,
        "session_reset": session_reset,
        "week_pct": week_pct,
        "week_reset": week_reset,
        "week_sonnet_pct": week_sonnet_pct,
    }


def _scrape_session_name() -> str:
    return f"{SCRAPE_PREFIX}{uuid.uuid4().hex[:8]}"


def _sleep(seconds: float) -> None:
    """Indireção testável do sleep do scraping."""
    import time

    time.sleep(seconds)


def scrape_usage(
    *,
    server: libtmux.Server | None = None,
    boot_wait: float = _BOOT_WAIT,
    usage_wait: float = _USAGE_WAIT,
) -> dict | None:
    """Sobe uma sessão tmux efêmera, roda ``claude``, dispara ``/usage`` e parseia.

    Quota-light: ``/usage`` não manda prompt — apenas abre o painel de status do
    plano. Sequência: ``claude`` (boot) → Enter (dispensa o diálogo de "trust
    this folder?") → ``/usage`` Enter → espera → capture-pane → strip ANSI →
    :func:`parse_usage`.

    **Garantias de segurança**: o nome da sessão SEMPRE começa com ``sfusage-``
    e é morta no ``finally`` (com ``assert`` de prefixo). Em qualquer
    falha/timeout retorna ``None`` em vez de propagar — o loop de coleta nunca é
    derrubado por uma TUI travada.
    """
    srv = server if server is not None else libtmux.Server()
    name = _scrape_session_name()
    session = None
    try:
        session = srv.new_session(
            session_name=name,
            start_directory=str(Path.home()),
            detach=True,
            x=200,
            y=50,
        )
        pane = session.active_window.active_pane
        # ``cmd('send-keys', ...)`` (tmux cru) é o equivalente exato de
        # ``tmux send-keys "<txt>" Enter`` e se mostrou confiável para a TUI.
        pane.cmd("send-keys", "claude", "Enter")
        _sleep(boot_wait)
        # Diálogo "trust this folder?" em diretórios novos: o default é confiar,
        # então um Enter o dispensa (e vira input vazio ignorado quando ausente).
        pane.cmd("send-keys", "Enter")
        _sleep(2.0)
        pane.cmd("send-keys", "/usage", "Enter")
        _sleep(usage_wait)

        captured = pane.capture_pane()
        text = "\n".join(captured) if isinstance(captured, list) else str(captured or "")
        return parse_usage(strip_ansi(text))
    except Exception:  # noqa: BLE001 - TUI/tmux instável não pode derrubar o loop
        logger.exception("scrape_usage: falha no scraping")
        return None
    finally:
        # Cinto de segurança: NUNCA mate nada que não seja nossa sessão efêmera.
        assert name.startswith(SCRAPE_PREFIX)
        try:
            if srv.has_session(name, exact=True):
                srv.kill_session(name)
        except Exception:  # noqa: BLE001
            logger.warning("scrape_usage: falha ao matar sessão %s", name)


# --------------------------------------------------------------------------- #
# Persistência / leitura (doc único host_usage)
# --------------------------------------------------------------------------- #
async def persist_usage(
    db: AsyncIOMotorDatabase,
    usage: dict,
    *,
    collection: str = HOST_USAGE_COLLECTION,
) -> None:
    """Upsert do snapshot dos limites num doc ÚNICO (``key=host``).

    Doc persistido::

        {key:"host", session_pct, session_reset, week_pct, week_reset,
         week_sonnet_pct, scanned_at}
    """
    doc = {**usage, "key": HOST_USAGE_KEY, "scanned_at": datetime.now(UTC)}
    await db[collection].update_one(
        {"key": HOST_USAGE_KEY}, {"$set": doc}, upsert=True
    )


async def read_usage(
    db: AsyncIOMotorDatabase,
    *,
    max_age_seconds: float | None = None,
    collection: str = HOST_USAGE_COLLECTION,
) -> dict | None:
    """Lê o doc ``host_usage`` e devolve ``metrics["limits"]`` (ou ``None``).

    Leitura BARATA (1 find_one) — usada pelo discovery ao enriquecer métricas.
    Quando ``max_age_seconds`` é dado, descarta o doc se for mais velho que o
    limite (snapshot obsoleto → ``None``). Retorna só os campos de limites
    (sem ``key``/``scanned_at``).
    """
    doc = await db[collection].find_one({"key": HOST_USAGE_KEY})
    if not doc:
        return None
    if max_age_seconds is not None:
        scanned = doc.get("scanned_at")
        if not isinstance(scanned, datetime):
            return None
        if scanned.tzinfo is None:
            scanned = scanned.replace(tzinfo=UTC)
        if (datetime.now(UTC) - scanned).total_seconds() >= max_age_seconds:
            return None
    return {
        "session_pct": doc.get("session_pct"),
        "session_reset": doc.get("session_reset"),
        "week_pct": doc.get("week_pct"),
        "week_reset": doc.get("week_reset"),
        "week_sonnet_pct": doc.get("week_sonnet_pct"),
    }
