"""Testes do _clean_for_speech (jarvis): pontuação solta não pode virar 'ponto'.

Unit puro (sem tmux/mongo/rabbit) — o import do jarvis é leve aqui.
"""

from __future__ import annotations

import pytest

import base64
import subprocess

import sessionflow_worker.jarvis as jarvis_mod
from sessionflow_worker.jarvis import _clean_for_speech, _owner_display_name, _summary_sys


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Pontuação solta/repetida → some (não vira "ponto, ponto" no TTS).
        # Ponto (interno, não-borda) vira VÍRGULA (decisão do projeto: o TTS
        # falava a palavra "ponto" em voz alta; vírgula dá a mesma pausa e
        # não é lida — ver comentário "PONTO → VÍRGULA" em _clean_for_speech).
        ("Sessão pratinha. . resumo", "Sessão pratinha, resumo"),
        ("Sessão pratinha... terminei", "Sessão pratinha, terminei"),
        ("deploy. , feito", "deploy, feito"),
        ("ok .. pronto", "ok, pronto"),
        # Frase normal: ponto interno vira vírgula (pausa, não a palavra "ponto");
        # ponto final cai por estar na borda.
        ("Primeira frase. Segunda frase.", "Primeira frase, Segunda frase"),
        # Markdown/símbolos somem.
        ("item *negrito* e (paren) #tag", "item negrito e paren tag"),
        # Ponto entre letras/números (arquivo/versão/decimal) NÃO vira "ponto".
        ("Editei detalhe.component.ts e app.css", "Editei detalhe component ts e app css"),
        ("Atualizei pra Opus 4.8", "Atualizei pra Opus 4 8"),
    ],
)
def test_clean_for_speech_drops_loose_punctuation(raw: str, expected: str) -> None:
    assert _clean_for_speech(raw) == expected


def test_clean_for_speech_strips_edges_and_urls() -> None:
    out = _clean_for_speech("  ... veja https://x.com/y agora.  ")
    assert "http" not in out
    assert not out.startswith(".")
    assert not out.endswith(".")


def test_owner_display_name_from_email(monkeypatch: pytest.MonkeyPatch) -> None:
    # Deriva do local-part do e-mail, primeiro token antes de "." (mesma lógica
    # do Perfil no frontend) — evita "você" ambíguo no resumo falado.
    monkeypatch.setenv("SESSIONFLOW_EMAIL", "heverton.pablo@example.com")
    assert _owner_display_name() == "Heverton"


def test_owner_display_name_empty_without_email(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SESSIONFLOW_EMAIL", raising=False)
    assert _owner_display_name() == ""


def test_summary_sys_defines_agent_vs_owner_roles() -> None:
    # Regressão do feedback do usuário: "a pessoa... não sei o que lá, só que é
    # você... é o agente" — o prompt precisa deixar claro que o AGENTE é
    # terceira pessoa e o DONO é chamado pelo nome, nunca um "você" ambíguo.
    prompt = _summary_sys("Diego")
    assert "Diego" in prompt
    assert "TERCEIRA PESSOA" in prompt
    assert "agente" in prompt.lower()


def test_summary_sys_falls_back_to_voce_without_owner() -> None:
    prompt = _summary_sys("")
    assert "você" in prompt.lower()


def test_voice_effect_disabled_returns_original_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jarvis_mod, "VOICE_EFFECT", "")
    called = False

    def fake_run(*a, **kw):  # noqa: ANN001, ANN002, ANN003
        nonlocal called
        called = True
        raise AssertionError("não deveria chamar ffmpeg com efeito desligado")

    monkeypatch.setattr(subprocess, "run", fake_run)
    audio = ("ZmFrZQ==", "audio/ogg")  # base64 de "fake"
    assert jarvis_mod._apply_voice_effect_sync(audio) == audio
    assert not called


def test_voice_effect_falls_back_to_original_on_ffmpeg_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(jarvis_mod, "VOICE_EFFECT", "chorus=0.6:0.9:60:0.4:0.25:2")

    def fake_run(*a, **kw):  # noqa: ANN001, ANN002, ANN003
        raise subprocess.CalledProcessError(1, "ffmpeg")

    monkeypatch.setattr(subprocess, "run", fake_run)
    original = base64.b64encode(b"fake-audio-bytes").decode("ascii")
    assert jarvis_mod._apply_voice_effect_sync((original, "audio/ogg")) == (
        original,
        "audio/ogg",
    )
