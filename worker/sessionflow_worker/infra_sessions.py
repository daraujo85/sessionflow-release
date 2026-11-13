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

Além dos nomes literais, o PREFIXO ``sfinfra-`` é reservado: nomes literais
curtos ("cf") podem colidir com um nome que o usuário inventar, e aí a
proteção viraria um bloqueio surpresa em vez de uma reserva explícita. Com o
prefixo, o selfboot passa a nomear o túnel ``sfinfra-tunnel`` — nome longo,
inequívoco e auto-documentado.
"""

from __future__ import annotations

#: Sessões tmux de infraestrutura — nunca são conversas de agente.
INFRA_SESSION_NAMES = frozenset(
    {
        "sessionflow-worker",
        "cloudflared-tunnel",
        "sessionflow-autoupdate",
        # Nome histórico do túnel no selfboot de Colab/Kaggle. Mantido na
        # allowlist porque notebooks já no ar têm a sessão com esse nome —
        # protegê-la evita que o app/worker a mate.
        "cf",
    }
)

#: Prefixo RESERVADO para infraestrutura criada pelo SessionFlow.
INFRA_SESSION_PREFIX = "sfinfra-"

#: Prefixos de sessões internas/efêmeras (scrapers de uso/modelos).
INTERNAL_SESSION_PREFIXES = ("sfusage-", "sfmodel-")


def is_infra_session(name: str | None) -> bool:
    """True se ``name`` é uma sessão de INFRAESTRUTURA (túnel/worker/etc.).

    Cobre o prefixo reservado ``sfinfra-`` E os nomes literais (incluindo
    o histórico "cf"). É a allowlist de REFERÊNCIA: todo nome de
    infraestrutura novo entra aqui e nos dois lados (worker + API).
    """
    if not name:
        return False
    if name in INFRA_SESSION_NAMES:
        return True
    return name.startswith(INFRA_SESSION_PREFIX)


def is_infra_or_internal_session(name: str | None) -> bool:
    """True se o nome pertence a sessões de infraestrutura ou scrapers efêmeros."""
    if not name:
        return True
    if is_infra_session(name):
        return True
    return name.startswith(INTERNAL_SESSION_PREFIXES)
