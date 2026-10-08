from app.voice.vad import Endpointer


def test_speech_start_fires_on_first_voiced_frame():
    ep = Endpointer(silence_ms_to_end=400, frame_ms=20)
    assert ep.feed(is_speech=True) == "speech_start"
    assert ep.feed(is_speech=True) is None  # already in speech


def test_utterance_end_fires_after_silence_threshold():
    ep = Endpointer(silence_ms_to_end=400, frame_ms=20)
    ep.feed(is_speech=True)  # speech_start
    # 400ms / 20ms = 20 silent frames needed
    for _ in range(19):
        assert ep.feed(is_speech=False) is None
    assert ep.feed(is_speech=False) == "utterance_end"


def test_brief_pause_does_not_end_utterance():
    ep = Endpointer(silence_ms_to_end=400, frame_ms=20)
    ep.feed(is_speech=True)
    for _ in range(5):  # 100ms pause, below threshold
        ep.feed(is_speech=False)
    assert ep.feed(is_speech=True) is None  # resumes, no new event
