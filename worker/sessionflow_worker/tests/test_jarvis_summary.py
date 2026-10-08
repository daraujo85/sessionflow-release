"""Testes do pipeline pane → resumo (Ollama) → TTS: cenários de fallback gracioso.

Unit puro (sem tmux/mongo/rabbit/Ollama reais) — tudo mockado via monkeypatch.
"""

from __future__ import annotations

import urllib.error

import pytest

import sessionflow_worker.jarvis as jarvis_mod
from sessionflow_worker.jarvis import _summary, _synth


# --- Resumo (pane → Ollama) ---------------------------------------------------


@pytest.mark.asyncio
async def test_summary_pane_vazio_usa_fallback_sem_chamar_ollama(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*a, **kw):  # noqa: ANN001, ANN002, ANN003
        raise AssertionError("não deveria chamar Ollama com pane vazio")

    monkeypatch.setattr(jarvis_mod, "_ollama_sync", fail)
    out = await _summary("   ", title="Sessão X aguarda você", desc="")
    assert out == "Sessão X aguarda você"


@pytest.mark.asyncio
async def test_summary_pane_normal_chama_ollama_e_usa_resposta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        jarvis_mod, "_ollama_sync", lambda system, prompt, **kw: "Resumo curto da tela."
    )
    out = await _summary("$ npm test\nPASS 12 suites", title="build", desc="build ocioso")
    assert out == "Resumo curto da tela."


@pytest.mark.asyncio
async def test_summary_ollama_offline_cai_pro_evento_sem_levantar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*a, **kw):  # noqa: ANN001, ANN002, ANN003
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(jarvis_mod, "_ollama_sync", boom)
    out = await _summary("algum conteudo de tela", title="Sessão Y aguarda você", desc="")
    assert out == "Sessão Y aguarda você"


@pytest.mark.asyncio
async def test_summary_ollama_404_modelo_ausente_cai_pro_evento(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regressão: modelo default não instalado (llama3.1:8b) -> 404 -> fallback."""

    def not_found(*a, **kw):  # noqa: ANN001, ANN002, ANN003
        raise urllib.error.HTTPError("http://x/api/generate", 404, "Not Found", {}, None)

    monkeypatch.setattr(jarvis_mod, "_ollama_sync", not_found)
    out = await _summary("algum conteudo de tela", title="build", desc="build ocioso")
    assert out == "build ocioso"


# --- TTS (resumo → voz) -------------------------------------------------------


@pytest.mark.asyncio
async def test_synth_tts_offline_degrada_pra_none_sem_levantar(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """xtts fora do ar E say indisponível (ex.: host não-macOS) -> None, sem exceção."""
    monkeypatch.setattr(jarvis_mod, "TTS_MODE", "xtts")

    def xtts_fail(text: str):  # noqa: ANN001
        raise ConnectionRefusedError("xtts offline")

    def say_fail(text: str):  # noqa: ANN001
        return None

    monkeypatch.setattr(jarvis_mod, "_synth_xtts_sync", xtts_fail)
    monkeypatch.setattr(jarvis_mod, "_synth_say_sync", say_fail)
    result = await _synth("resumo falado de teste")
    assert result is None


@pytest.mark.asyncio
async def test_synth_xtts_offline_cai_pro_say(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jarvis_mod, "TTS_MODE", "xtts")
    monkeypatch.setattr(jarvis_mod, "VOICE_EFFECT", "")

    def xtts_fail(text: str):  # noqa: ANN001
        raise ConnectionRefusedError("xtts offline")

    monkeypatch.setattr(jarvis_mod, "_synth_xtts_sync", xtts_fail)
    monkeypatch.setattr(jarvis_mod, "_synth_say_sync", lambda text: ("YXVkaW8=", "audio/ogg"))
    result = await _synth("resumo falado de teste")
    assert result == ("YXVkaW8=", "audio/ogg")
