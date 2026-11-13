"""Sessões tmux de INFRAESTRUTURA do SessionFlow (não são conversas de agente).

Espelha ``worker/sessionflow_worker/infra_sessions.py``. São dois processos
separados (API em Docker, worker no host), então a lista é duplicada de
propósito — a API não pode importar o pacote do worker.

Sessões de infraestrutura NUNCA podem ser Presentedas na lista de Sessões nem
mortas por comando: ``cf`` é a sessão do TÚNEL cloudflared (proxy de
Mongo+RabbitMQ) nos hosts Colab/Kaggle, e ``sessionflow-worker`` é o próprio
daemon. Matar qualquer uma delas tira o host do ar — incidente real de
nov/2026: um ``kill cf`` enfileirado (host offline) matou o túnel quando o
worker voltou, e o host ficou travado em "Ligando" pra sempre.
"""

from __future__ import annotations

#: Sessões tmux de infraestrutura — nunca são conversas de agente.
INFRA_SESSION_NAMES = frozenset(
    {
        "sessionflow-worker",
        "cloudflared-tunnel",
        "sessionflow-autoupdate",
        "cf",
    }
)


def is_infra_session(name: str | None) -> bool:
    """True se ``name`` é uma sessão de INFRAESTRUTURA (túnel/worker/etc.)."""
    return bool(name) and name in INFRA_SESSION_NAMES
