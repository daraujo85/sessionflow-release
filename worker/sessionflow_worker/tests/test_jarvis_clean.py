"""Testes do _clean_for_speech (jarvis): pontuação solta não pode virar 'ponto'.

Unit puro (sem tmux/mongo/rabbit) — o import do jarvis é leve aqui.
"""

from __future__ import annotations

import pytest

from sessionflow_worker.jarvis import _clean_for_speech


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Pontuação solta/repetida → some (não vira "ponto, ponto" no TTS).
        ("Sessão pratinha. . resumo", "Sessão pratinha. resumo"),
        ("Sessão pratinha... terminei", "Sessão pratinha. terminei"),
        ("deploy. , feito", "deploy. feito"),
        ("ok .. pronto", "ok. pronto"),
        # Frase normal: ponto interno é pausa (preservado), ponto final cai na borda.
        ("Primeira frase. Segunda frase.", "Primeira frase. Segunda frase"),
        # Markdown/símbolos somem.
        ("item *negrito* e (paren) #tag", "item negrito e paren tag"),
    ],
)
def test_clean_for_speech_drops_loose_punctuation(raw: str, expected: str) -> None:
    assert _clean_for_speech(raw) == expected


def test_clean_for_speech_strips_edges_and_urls() -> None:
    out = _clean_for_speech("  ... veja https://x.com/y agora.  ")
    assert "http" not in out
    assert not out.startswith(".")
    assert not out.endswith(".")
