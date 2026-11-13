"""Sessões tmux de INFRAESTRUTURA do SessionFlow (não são conversas de agente).

Vive num módulo próprio (sem imports) porque DUAS camadas precisam da mesma
lista, e uma delas ficaria num ciclo de import se buscasse na outra:

- ``discovery``: não pode descobrir/upsertar essas sessões, senão elas
  aparecem na tela de Sessões como trabalho e podem ser apagadas por engano;
- ``command_consumer``: não pode matar essas sessões, nem quando o comando
  chega enfileirado de uma sessão antiga (host ficou offline).

Incidente real (nov/2026): o selfboot de Colab/Kaggle nomeia a sessão do
TÚNEL cloudflared de ``cf`` (proxy de Mongo+RabbitMQ em 127.0.0.1). Esse
nome não estava na lista de infra, então ``cf`` apareceu como sessão de
trabalho. A limpeza de 07/11 publishes "kill cf"; com o host offline o
comando ficou na fila do RabbitMQ (o TTL de 300s não cobre ``kill``) e, quando
o worker voltou, MATOU O PRÓPRIO TÚNEL — o heartbeat deixou de chegar e o
host ficou travado em "Ligando" pra sempre.

Por convenção o selfboot cria o worker em ``sessionflow-worker`` e o túnel em
``cf``; o Duck/Mac usam ``cloudflared-tunnel``. Todos são infraestrutura.
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

#: Prefixos de sessões internas/efêmeras (scrapers de uso/modelos).
INTERNAL_SESSION_PREFIXES = ("sfusage-", "sfmodel-")


def is_infra_session(name: str | None) -> bool:
    """True se ``name`` é uma sessão de INFRAESTRUTURA (túnel/worker/etc.)."""
    return bool(name) and name in INFRA_SESSION_NAMES


def is_infra_or_internal_session(name: str | None) -> bool:
    """True se o nome pertence a sessões de infraestrutura ou scrapers efêmeros."""
    if not name:
        return True
    if name in INFRA_SESSION_NAMES:
        return True
    return name.startswith(INTERNAL_SESSION_PREFIXES)
