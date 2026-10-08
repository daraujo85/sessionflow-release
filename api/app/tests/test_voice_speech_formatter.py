from app.voice.speech_formatter import to_speech_text, SentenceChunker


def test_strips_code_blocks_and_urls():
    display = "Veja `foo()` e https://example.com/x. Corrigido!"
    speech = to_speech_text(display)
    assert "https://" not in speech
    assert "`" not in speech


def test_strips_stack_trace():
    display = "Resposta.\nTraceback (most recent call last):\n  File 'app.py', line 8, in run\n    raise ValueError('falhou')\nValueError: falhou\nFim."
    speech = to_speech_text(display)
    assert "Traceback" not in speech
    assert "ValueError" not in speech
    assert speech == "Resposta. Fim."


def test_strips_markdown_table():
    display = "Resumo:\n| sessão | estado |\n| --- | --- |\n| pagamentos | running |\nPronto."
    speech = to_speech_text(display)
    assert "pagamentos" not in speech
    assert "|" not in speech
    assert speech == "Resumo: Pronto."


def test_chunker_flushes_on_sentence_end():
    c = SentenceChunker(max_chars=100)
    chunks = c.feed("Encontrei o problema. O teste falha porque")
    assert chunks == ["Encontrei o problema."]


def test_chunker_flushes_on_max_chars():
    c = SentenceChunker(max_chars=10)
    chunks = c.feed("um texto bem mais longo sem pontuação nenhuma")
    assert chunks  # flushed at least once before hitting the char cap
    assert all(len(chunk) <= 10 or " " in chunk for chunk in chunks)


def test_flush_returns_remaining_partial_text():
    c = SentenceChunker(max_chars=100)
    c.feed("frase sem fim")
    assert c.flush() == "frase sem fim"
    assert c.flush() is None  # nothing left
