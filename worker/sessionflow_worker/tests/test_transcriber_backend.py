"""Testes do dispatch de backend em ``transcriber.py`` (puro — sem carregar

modelo real de nenhum dos dois). Cobre a escolha mlx vs faster-whisper por
plataforma/override e o roteamento pro modelo default de cada um.
"""

from __future__ import annotations

import pytest

from sessionflow_worker import transcriber


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSIONFLOW_TRANSCRIBE_BACKEND", raising=False)
    transcriber._BACKEND_OVERRIDE = ""


def test_resolves_mlx_on_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcriber.platform, "system", lambda: "Darwin")
    assert transcriber._resolve_backend() == "mlx"


def test_resolves_faster_whisper_off_darwin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcriber.platform, "system", lambda: "Linux")
    assert transcriber._resolve_backend() == "faster-whisper"


def test_explicit_override_wins_over_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcriber.platform, "system", lambda: "Darwin")
    transcriber._BACKEND_OVERRIDE = "faster-whisper"
    assert transcriber._resolve_backend() == "faster-whisper"


def test_default_model_matches_backend() -> None:
    assert transcriber._default_model_for("mlx") == transcriber.DEFAULT_MODEL_MLX
    assert (
        transcriber._default_model_for("faster-whisper")
        == transcriber.DEFAULT_MODEL_FASTER_WHISPER
    )


async def test_transcribe_dispatches_to_resolved_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """``transcribe()`` chama a função certa com o modelo default certo,

    sem precisar importar mlx_whisper/faster_whisper de verdade.
    """
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")

    calls: list[tuple[str, str, str | None]] = []

    def _fake_faster_whisper(path: str, model_name: str, language: str | None) -> str:
        calls.append(("faster-whisper", model_name, language))
        return "ok"

    monkeypatch.setattr(transcriber.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        transcriber, "_transcribe_sync_faster_whisper", _fake_faster_whisper
    )

    text = await transcriber.transcribe(str(audio))

    assert text == "ok"
    assert calls == [
        ("faster-whisper", transcriber.DEFAULT_MODEL_FASTER_WHISPER, "pt")
    ]


async def test_transcribe_missing_file_raises_before_touching_backend(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        await transcriber.transcribe(str(tmp_path / "nope.wav"))
