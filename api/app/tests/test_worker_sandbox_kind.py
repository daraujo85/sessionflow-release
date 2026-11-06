"""Testes da identidade SANDBOX declarada pelo worker (sandbox_kind).

Contexto
--------
A identificação de host Kaggle nascia de dois sinais frágeis: uma lista
chumbada de ``host_id`` (``KAGGLE_HOST_IDS``) e a substring "kaggle" no nome.
Nenhum dos dois alcança a segunda conta (engdiegoaraujo) nem um notebook
recriado — e o ``display_name`` de um runtime Kaggle é o hash do container
(``fc1027582ecb``), sem a palavra "kaggle".

O worker agora publica ``sandbox_kind`` + a URL/ID do notebook no heartbeat,
lidos do ambiente do selfboot. Estes testes fixam o contrato do lado da API:
o campo sai no ``WorkerOut`` e o ``sandbox_kind`` explícito vence a heurística.
"""

from __future__ import annotations

from app.routers.worker import (
    DEFAULT_KAGGLE_NOTEBOOK_URL,
    WorkerOut,
    _is_kaggle_host,
    _to_worker_out,
    build_sandbox_url,
)


def test_worker_out_expoe_sandbox_kind() -> None:
    """O campo precisa sair na API — senão o frontend não tem como ler."""
    assert "sandbox_kind" in WorkerOut.model_fields


def test_worker_out_repassa_identidade_kaggle() -> None:
    doc = {
        "_id": "h_novo",
        "display_name": "9f3ab21c0d77",  # hash do container, sem "kaggle"
        "hostname": "9f3ab21c0d77",
        "sandbox_kind": "kaggle",
        "kaggle_notebook_url": "https://www.kaggle.com/code/engdiegoaraujo/notebookbe7faf1c38",
    }
    out = _to_worker_out(doc)
    assert out.sandbox_kind == "kaggle"
    assert out.kaggle_notebook_url.endswith("notebookbe7faf1c38")


def test_sandbox_kind_explicito_identifica_host_desconhecido() -> None:
    """Host_id nunca visto + nome que é hash: o campo declarado resolve."""
    doc = {
        "display_name": "9f3ab21c0d77",
        "hostname": "9f3ab21c0d77",
        "sandbox_kind": "kaggle",
        "kaggle_notebook_url": "https://www.kaggle.com/code/engdiegoaraujo/notebookbe7faf1c38",
    }
    assert _is_kaggle_host(doc, "9f3ab21c0d77") is True
    assert build_sandbox_url(doc, "9f3ab21c0d77") == (
        "https://www.kaggle.com/code/engdiegoaraujo/notebookbe7faf1c38/edit"
    )


def test_segunda_conta_nao_cai_no_notebook_default() -> None:
    """Regressão: sem URL própria, o card abriria o notebook da 1ª conta."""
    doc = {
        "sandbox_kind": "kaggle",
        "kaggle_notebook_url": "https://www.kaggle.com/code/engdiegoaraujo/notebookbe7faf1c38",
    }
    url = build_sandbox_url(doc, "h_conta2")
    assert url is not None
    assert "engdiegoaraujo" in url
    assert DEFAULT_KAGGLE_NOTEBOOK_URL not in url


def test_host_comum_com_sandbox_kind_ausente_nao_e_kaggle() -> None:
    """Macbook/Duck/Deepin continuam sem botão de sandbox."""
    doc = {"display_name": "Macbook Air", "hostname": "ip-192-168-31-102.ec2.internal"}
    assert _is_kaggle_host(doc, "f5efcc44-7b0a-41d2-a50b-84b12774edc0") is False
    assert build_sandbox_url(doc, "f5efcc44-7b0a-41d2-a50b-84b12774edc0") is None
