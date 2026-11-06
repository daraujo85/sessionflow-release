"""Testes da identidade SANDBOX no heartbeat (Colab/Kaggle).

Por que isto existe
-------------------
O app precisa saber se um host é um notebook efêmero — e de QUAL notebook,
porque o botão Ligar abre essa URL. Até agora a API/frontend adivinhavam por
``host_id`` chumbado (``KAGGLE_HOST_IDS``) e por substring do nome, o que
quebra em dois casos reais:

* o ``display_name`` de um runtime Kaggle é o hash do container
  (``fc1027582ecb``) — não contém "kaggle" em lugar nenhum;
* cada conta/notebook novo gera host_ids novos, então a lista chumbada nunca
  alcança a segunda conta (engdiegoaraujo) nem um notebook recriado.

O contrato: o worker publica no próprio doc de status a plataforma e a URL do
notebook, lidas do ambiente que o selfboot prepara. Aí a identificação vira
dado, não heurística.
"""

from __future__ import annotations

import pytest

from sessionflow_worker.runner import sandbox_identity


def test_kaggle_url_vira_identidade(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "SF_KAGGLE_URL", "https://www.kaggle.com/code/engdiegoaraujo/notebookbe7faf1c38"
    )
    monkeypatch.delenv("SF_COLAB_NOTEBOOK_ID", raising=False)
    ident = sandbox_identity()
    assert ident["sandbox_kind"] == "kaggle"
    assert ident["kaggle_notebook_url"].endswith("notebookbe7faf1c38")


def test_kaggle_url_nao_duplica_edit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Secret pode vir com /edit colado da barra de endereço — normaliza."""
    monkeypatch.setenv(
        "SF_KAGGLE_URL", "https://www.kaggle.com/code/x/y/edit"
    )
    monkeypatch.delenv("SF_COLAB_NOTEBOOK_ID", raising=False)
    ident = sandbox_identity()
    assert ident["kaggle_notebook_url"] == "https://www.kaggle.com/code/x/y"
    assert ident["sandbox_kind"] == "kaggle"


def test_colab_por_notebook_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SF_KAGGLE_URL", raising=False)
    monkeypatch.setenv("SF_COLAB_NOTEBOOK_ID", "1WRlvNXyQGyYZ9xURL5gN-7ZmmdHRBi7A")
    monkeypatch.setenv("SF_COLAB_AUTHUSER", "1")
    ident = sandbox_identity()
    assert ident["sandbox_kind"] == "colab"
    assert ident["colab_notebook_id"] == "1WRlvNXyQGyYZ9xURL5gN-7ZmmdHRBi7A"
    assert ident["colab_authuser"] == "1"


def test_host_comum_nao_declara_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    """Macbook/Duck/Deepin: dicionário vazio, nenhum campo inventado."""
    for nome in ("SF_KAGGLE_URL", "SF_COLAB_NOTEBOOK_ID", "SF_COLAB_AUTHUSER"):
        monkeypatch.delenv(nome, raising=False)
    assert sandbox_identity() == {}


def test_kaggle_vence_colab(monkeypatch: pytest.MonkeyPatch) -> None:
    """Se os dois estiverem setados (env vazado), Kaggle é o específico."""
    monkeypatch.setenv("SF_KAGGLE_URL", "https://www.kaggle.com/code/a/b")
    monkeypatch.setenv("SF_COLAB_NOTEBOOK_ID", "abc123")
    assert sandbox_identity()["sandbox_kind"] == "kaggle"


@pytest.mark.asyncio
async def test_heartbeat_grava_a_identidade_no_doc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fim a fim: o campo tem que chegar no $set do doc de status.

    Sem isto a função fica bonita e inerte — a API continuaria sem saber que
    o host é Kaggle e o card seguiria sem botão.
    """
    import asyncio as _asyncio

    from sessionflow_worker import runner as runner_mod

    monkeypatch.setenv("SF_KAGGLE_URL", "https://www.kaggle.com/code/engdiegoaraujo/notebookbe7faf1c38")
    monkeypatch.delenv("SF_COLAB_NOTEBOOK_ID", raising=False)

    capturado: dict = {}

    class _Coll:
        async def update_one(self, filtro, update, upsert=False):
            capturado.update(update["$set"])
            raise _asyncio.CancelledError  # sai do loop infinito após 1 volta

    class _DB:
        def __getitem__(self, _nome):
            return _Coll()

    with pytest.raises(_asyncio.CancelledError):
        await runner_mod.heartbeat_loop(_DB(), "h1", "linux", {}, interval=0.01)

    assert capturado.get("sandbox_kind") == "kaggle", f"não gravou: {sorted(capturado)}"
    assert "notebookbe7faf1c38" in capturado.get("kaggle_notebook_url", "")
