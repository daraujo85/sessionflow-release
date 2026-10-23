"""Unit puro de _to_worker_out (sem Mongo)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.routers.worker import (
    DEFAULT_COLAB_NOTEBOOK_ID,
    _to_worker_out,
    build_colab_url,
)


def test_passes_metrics_and_agents() -> None:
    doc = {
        "_id": "h1", "updated_at": datetime.now(UTC),
        "metrics": {"cpu_pct": 10.0, "mem_avail_gb": 4.0},
        "agents": {"claude": True},
    }
    out = _to_worker_out(doc)
    assert out.metrics == {"cpu_pct": 10.0, "mem_avail_gb": 4.0}
    assert out.agents == {"claude": True}


def test_old_doc_has_none() -> None:
    out = _to_worker_out({"_id": "h1", "updated_at": datetime.now(UTC)})
    assert out.metrics is None
    assert out.agents is None


def test_passes_colab_config() -> None:
    """WorkerStatus expõe colab_notebook_id + colab_authuser pro frontend."""
    out = _to_worker_out({
        "_id": "h_colab",
        "updated_at": datetime.now(UTC),
        "colab_notebook_id": "1ABC",
        "colab_authuser": "2",
    })
    assert out.colab_notebook_id == "1ABC"
    assert out.colab_authuser == "2"


def test_online_is_heartbeat_only() -> None:
    """Só heartbeat recente conta; flag `online` legada do /start é ignorada."""
    now = datetime.now(UTC)
    old = now - timedelta(minutes=5)
    assert _to_worker_out({"_id": "h", "updated_at": now}).online is True
    # /start antigo gravava online=True sem worker vivo → era 🟢 falso.
    assert _to_worker_out({"_id": "h", "online": True, "updated_at": old}).online is False
    assert _to_worker_out({"_id": "h", "online": True}).online is False
    # /stop antigo gravava online=False e o heartbeat nunca limpava → host vivo ficava 🔴.
    assert _to_worker_out({"_id": "h", "online": False, "updated_at": now}).online is True


def test_starting_is_separate_from_online() -> None:
    """POST /start recente sem heartbeat → starting (🟡), NÃO online."""
    now = datetime.now(UTC)
    old = now - timedelta(minutes=5)
    out = _to_worker_out({"_id": "h_colab", "starting_at": now, "updated_at": old})
    assert out.online is False
    assert out.starting is True
    # Grace vencido → nem online nem starting.
    stale = _to_worker_out({"_id": "h_colab", "starting_at": now - timedelta(hours=1), "updated_at": old})
    assert stale.starting is False
    # Heartbeat chegou → online, starting some.
    up = _to_worker_out({"_id": "h_colab", "starting_at": now, "updated_at": now})
    assert up.online is True
    assert up.starting is False


# --- build_colab_url ---------------------------------------------------------
# Heurística de URL do notebook Colab: configurável via `colab_notebook_id`/
# `colab_authuser` no `worker_status`, com fallback pro DEFAULT_COLAB_NOTEBOOK_ID
# e heurística legada ("colab 2"/"colab2"/"02151543" → authuser=1).


def test_build_colab_url_colab1_default() -> None:
    """Colab 1 sem config → notebook padrão + authuser=0 (heurística)."""
    doc = {"display_name": "Colab 1 (CPU)"}
    assert build_colab_url(doc, "h_colab1") == (
        f"https://colab.research.google.com/drive/{DEFAULT_COLAB_NOTEBOOK_ID}?authuser=0"
    )


def test_build_colab_url_colab2_heuristic() -> None:
    """'Colab 2' sem config → notebook padrão + authuser=1 (heurística)."""
    doc = {"display_name": "Colab 2 Pro (GPU T4)"}
    assert build_colab_url(doc, "h_colab2") == (
        f"https://colab.research.google.com/drive/{DEFAULT_COLAB_NOTEBOOK_ID}?authuser=1"
    )


def test_build_colab_url_colab2_by_host_id() -> None:
    """host_id '02151543' (Colab 2 conhecido) → authuser=1 mesmo sem 'colab 2' no label."""
    doc = {"display_name": "Colab Pro T4", "hostname": "pro-t4"}
    assert build_colab_url(doc, "02151543") == (
        f"https://colab.research.google.com/drive/{DEFAULT_COLAB_NOTEBOOK_ID}?authuser=1"
    )


def test_build_colab_url_custom_notebook() -> None:
    """`colab_notebook_id` próprio no doc sobrescreve o DEFAULT."""
    doc = {"display_name": "Colab 3", "colab_notebook_id": "1ABCdef_notebook"}
    assert build_colab_url(doc, "h_colab3") == (
        "https://colab.research.google.com/drive/1ABCdef_notebook?authuser=0"
    )


def test_build_colab_url_custom_authuser() -> None:
    """`colab_authuser` próprio no doc sobrescreve a heurística legada."""
    # Sem override seria 0 (Colab 1) — com 2 vira 2
    doc = {"display_name": "Colab 1 (CPU)", "colab_authuser": "2"}
    assert build_colab_url(doc, "h_colab1") == (
        f"https://colab.research.google.com/drive/{DEFAULT_COLAB_NOTEBOOK_ID}?authuser=2"
    )


def test_build_colab_url_non_colab_returns_none() -> None:
    """Host sem 'colab' no nome/host_id → None (evita abrir URL Drive em Mac/Duck)."""
    doc = {"display_name": "Macbook Air"}
    assert build_colab_url(doc, "h_mac") is None
    # host_id também conta como sinal
    doc2 = {"display_name": "Pro", "hostname": "renderer-lnx"}
    assert build_colab_url(doc2, "h_dell") is None


def test_build_colab_url_both_custom() -> None:
    """notebook_id E authuser custom — ambos sobrescrevem."""
    doc = {
        "display_name": "Colab 2 Pro",
        "colab_notebook_id": "1XYZ_my_colab2_nb",
        "colab_authuser": "1",
    }
    assert build_colab_url(doc, "h_colab2") == (
        "https://colab.research.google.com/drive/1XYZ_my_colab2_nb?authuser=1"
    )

