"""Session state machine (TMUX-12).

Derivação pura do estado de uma sessão a partir de sinais observáveis.
"""

from __future__ import annotations

from enum import Enum


class SessionState(str, Enum):
    """Estados possíveis de uma sessão."""

    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_EXTERNAL = "waiting_external"
    COMPLETED = "completed"
    ERROR = "error"
    STOPPED = "stopped"
    DETACHED = "detached"


def derive_state(
    tmux_present: bool,
    attached: bool,
    agent_alive: bool,
    exit_code: int | None,
) -> SessionState:
    """Deriva o estado determinístico da sessão.

    Precedência (maior primeiro):
        stopped > error > detached > running

    Regras:
        - tmux ausente -> ``stopped`` (nada mais importa).
        - tmux presente e agente terminou com ``exit_code`` != 0 e não-None
          -> ``error``.
        - tmux presente e não anexado -> ``detached``.
        - tmux presente, anexado e agente vivo -> ``running``.

    Nota: ``waiting_input``, ``waiting_external`` e ``completed`` são estados
    semânticos refinados depois, na feature de captura de output; não são
    derivados aqui.
    """
    if not tmux_present:
        return SessionState.STOPPED

    if exit_code is not None and exit_code != 0:
        return SessionState.ERROR

    # Agente vivo = RUNNING, INDEPENDENTE de cliente tmux anexado: o SessionFlow
    # monitora sem TTY (nunca anexa), então amarrar "running" a "attached"
    # marcava toda sessão monitorada como detached (e exigia Retomar à toa).
    if agent_alive:
        return SessionState.RUNNING

    # Sem agente vivo → o processo do agente encerrou, sobrou só o tmux (e talvez
    # o shell). NÃO é "running": mandar comando não faz nada. Vira ``detached``
    # (a UI oferece Retomar), MESMO que um cliente tmux ainda esteja anexado
    # olhando o shell — anexado não significa agente vivo.
    return SessionState.DETACHED
