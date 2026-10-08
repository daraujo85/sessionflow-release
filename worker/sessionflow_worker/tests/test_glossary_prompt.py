"""Teste de ``_build_glossary_prompt()`` (função pura, sem Mongo/tmux)."""

from __future__ import annotations

from sessionflow_worker.command_consumer import (
    _GLOSSARY_PROMPT_MAX_CHARS,
    _build_glossary_prompt,
)


def test_empty_or_blank_returns_none() -> None:
    assert _build_glossary_prompt("") is None
    assert _build_glossary_prompt("   \n  ") is None


def test_splits_by_comma_and_newline_and_strips() -> None:
    raw = "QI Tech, BMS\nBMP ,  outro termo  "
    assert _build_glossary_prompt(raw) == (
        "Vocabulário técnico: QI Tech, BMS, BMP, outro termo"
    )


def test_truncates_above_max_chars() -> None:
    raw = ", ".join(f"termo{i}" for i in range(500))
    prompt = _build_glossary_prompt(raw)
    assert prompt is not None
    assert len(prompt) == _GLOSSARY_PROMPT_MAX_CHARS
