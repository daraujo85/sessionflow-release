import asyncio

import pytest

from app.voice.agent_output_bridge import bridge_agent_output


class FakeBroker:
    def __init__(self, events):
        self._events = events
        self.unsubscribed_queues = []

    async def subscribe(self):
        q = asyncio.Queue()
        for e in self._events:
            await q.put(e)
        await q.put({"type": "session_status", "session": "s1", "status": "waiting_input"})
        return q

    async def unsubscribe(self, queue) -> None:
        self.unsubscribed_queues.append(queue)

@pytest.mark.asyncio
async def test_bridge_forwards_output_lines_as_text_deltas():
    events = [
        {"session": "s1", "seq": 1, "text": "Encontrei o problema.", "line_type": "agent"},
        {"session": "s1", "seq": 2, "text": " O teste falha porque...", "line_type": "agent"},
    ]
    broker = FakeBroker(events)
    deltas = []
    done = asyncio.Event()

    async def on_delta(text):
        deltas.append(text)

    async def on_done():
        done.set()

    await bridge_agent_output("s1", broker, on_delta, on_done)
    assert deltas == ["Encontrei o problema.", " O teste falha porque..."]
    assert done.is_set()

@pytest.mark.asyncio
async def test_bridge_ignores_events_from_other_sessions():
    events = [
        {"session": "other", "seq": 1, "text": "não é pra essa sessão", "line_type": "agent"},
        {"session": "s1", "seq": 1, "text": "aqui sim", "line_type": "agent"},
    ]
    broker = FakeBroker(events)
    deltas = []

    async def on_delta(text):
        deltas.append(text)

    async def on_done():
        pass

    await bridge_agent_output("s1", broker, on_delta, on_done)
    assert deltas == ["aqui sim"]

@pytest.mark.asyncio
async def test_bridge_ignores_non_terminal_status_events():
    """A `session_status` event whose status isn't a done-status (e.g. the
    agent is still `working`) must not end the bridge."""
    events = [
        {"type": "session_status", "session": "s1", "status": "working"},
        {"session": "s1", "seq": 1, "text": "ainda trabalhando", "line_type": "agent"},
    ]
    broker = FakeBroker(events)
    deltas = []
    done = asyncio.Event()

    async def on_delta(text):
        deltas.append(text)

    async def on_done():
        done.set()

    await bridge_agent_output("s1", broker, on_delta, on_done)
    assert deltas == ["ainda trabalhando"]
    assert done.is_set()  # still ends on the FakeBroker's trailing waiting_input


@pytest.mark.asyncio
async def test_bridge_ignores_events_without_line_type():
    """Critical #3/#12: `jarvis_audio` frames and screen-mirror pushes carry
    a `text`/`session` pair too but no `line_type` — must not be spoken."""
    events = [
        {"type": "jarvis_audio", "session": "s1", "text": "audio blob, not a reply"},
        {"session": "s1", "seq": 1, "text": "reposta real", "line_type": "agent"},
    ]
    broker = FakeBroker(events)
    deltas = []

    async def on_delta(text):
        deltas.append(text)

    async def on_done():
        pass

    await bridge_agent_output("s1", broker, on_delta, on_done)
    assert deltas == ["reposta real"]


@pytest.mark.asyncio
async def test_bridge_ignores_snapshot_replayed_lines():
    """`output_capture.py::_publish` marca `snapshot=True` nas linhas que são
    o pane pré-existente reenviado por `start_capture` (ex.: após restart do
    worker) — não é resposta nova, não pode ser falada."""
    events = [
        {"session": "s1", "seq": 1, "text": "conversa antiga", "line_type": "agent", "snapshot": True},
        {"session": "s1", "seq": 2, "text": "resposta nova", "line_type": "agent", "snapshot": False},
    ]
    broker = FakeBroker(events)
    deltas = []

    async def on_delta(text):
        deltas.append(text)

    async def on_done():
        pass

    await bridge_agent_output("s1", broker, on_delta, on_done)
    assert deltas == ["resposta nova"]


@pytest.mark.asyncio
async def test_bridge_ignores_cmd_and_tool_lines():
    """Eco de comando (`cmd`) e trace de tool call (`tool`, ex.: `⎿ ...`)
    nunca são resposta — não falar."""
    events = [
        {"session": "s1", "seq": 1, "text": "$ ls -la", "line_type": "cmd"},
        {"session": "s1", "seq": 2, "text": "⎿ Read file.py", "line_type": "tool"},
        {"session": "s1", "seq": 3, "text": "resposta real", "line_type": "agent"},
    ]
    broker = FakeBroker(events)
    deltas = []

    async def on_delta(text):
        deltas.append(text)

    async def on_done():
        pass

    await bridge_agent_output("s1", broker, on_delta, on_done)
    assert deltas == ["resposta real"]


@pytest.mark.asyncio
async def test_bridge_ignores_lines_without_letters():
    """Spinner ("✢ ...") e moldura de caixa (╭─│╰) são só símbolo — sem
    nenhuma letra, mesmo com `line_type` falável (`out`/`sys`)."""
    events = [
        {"session": "s1", "seq": 1, "text": "✢ ✢ ✢ ···", "line_type": "out"},
        {"session": "s1", "seq": 2, "text": "╭──────────╮", "line_type": "sys"},
        {"session": "s1", "seq": 3, "text": "resposta real", "line_type": "out"},
    ]
    broker = FakeBroker(events)
    deltas = []

    async def on_delta(text):
        deltas.append(text)

    async def on_done():
        pass

    await bridge_agent_output("s1", broker, on_delta, on_done)
    assert deltas == ["resposta real"]


@pytest.mark.asyncio
async def test_bridge_ends_on_discovery_status_event():
    """`discovery.py` (worker) doesn't emit `type="session_status"` — it emits
    `type="status"` via `_emit`, with `status` flattened to the event's top
    level by `emit_event`'s `extra` handling. The bridge must recognize this
    real shape, not just the `session_status` one used in older tests."""
    events = [
        {"session": "s1", "seq": 1, "text": "trabalhando", "line_type": "agent"},
        {"type": "status", "session": "s1", "status": "waiting_input"},
    ]
    broker = FakeBroker(events)
    deltas = []
    done = asyncio.Event()

    async def on_delta(text):
        deltas.append(text)

    async def on_done():
        done.set()

    await bridge_agent_output("s1", broker, on_delta, on_done)
    assert deltas == ["trabalhando"]
    assert done.is_set()


@pytest.mark.asyncio
async def test_bridge_ignores_echo_of_own_input():
    """`route_session` digita o texto no tmux via send-keys; a CLI ecoa esse
    texto de volta na tela (linha nova, sem `snapshot`, `line_type` falável)
    — não é resposta do agente, é o próprio input do usuário reaparecendo."""
    events = [
        {"session": "s1", "seq": 1, "text": "> corrige o bug", "line_type": "out"},
        {"session": "s1", "seq": 2, "text": "Encontrei o problema.", "line_type": "agent"},
    ]
    broker = FakeBroker(events)
    deltas = []

    async def on_delta(text):
        deltas.append(text)

    async def on_done():
        pass

    await bridge_agent_output("s1", broker, on_delta, on_done, echo_text="corrige o bug")
    assert deltas == ["Encontrei o problema."]


@pytest.mark.asyncio
async def test_bridge_ignores_wrapped_echo_fragment():
    """Pergunta longa quebra em várias linhas na tela (reflow do terminal) —
    cada fragmento precisa ser reconhecido como eco, não só a linha inteira."""
    events = [
        {"session": "s1", "seq": 1, "text": "> corrige o bug do", "line_type": "out"},
        {"session": "s1", "seq": 2, "text": "login que trava", "line_type": "out"},
        {"session": "s1", "seq": 3, "text": "Encontrei o problema.", "line_type": "agent"},
    ]
    broker = FakeBroker(events)
    deltas = []

    async def on_delta(text):
        deltas.append(text)

    async def on_done():
        pass

    await bridge_agent_output(
        "s1", broker, on_delta, on_done, echo_text="corrige o bug do login que trava"
    )
    assert deltas == ["Encontrei o problema."]


@pytest.mark.asyncio
async def test_bridge_speaks_line_when_echo_text_not_given():
    """`echo_text=None` (default): sem base de comparação, nenhuma linha é
    descartada por eco — comportamento anterior preservado."""
    events = [
        {"session": "s1", "seq": 1, "text": "corrige o bug", "line_type": "out"},
    ]
    broker = FakeBroker(events)
    deltas = []

    async def on_delta(text):
        deltas.append(text)

    async def on_done():
        pass

    await bridge_agent_output("s1", broker, on_delta, on_done)
    assert deltas == ["corrige o bug"]


@pytest.mark.asyncio
async def test_bridge_unsubscribes_on_normal_completion():
    """Critical #3: the subscriber queue must be released when the bridge
    returns normally (terminal `session_status`) — otherwise every
    session-mode turn leaks a subscriber forever."""
    broker = FakeBroker([])

    async def on_delta(text):
        pass

    async def on_done():
        pass

    await bridge_agent_output("s1", broker, on_delta, on_done)
    assert len(broker.unsubscribed_queues) == 1


@pytest.mark.asyncio
async def test_bridge_unsubscribes_when_cancelled():
    """Critical #3: cancelling the bridge task while it's blocked on
    `queue.get()` must still release the subscriber queue via `finally`."""

    class BlockingBroker:
        def __init__(self) -> None:
            self.queue = asyncio.Queue()
            self.unsubscribed_queues = []

        async def subscribe(self):
            return self.queue

        async def unsubscribe(self, queue) -> None:
            self.unsubscribed_queues.append(queue)

    broker = BlockingBroker()

    async def on_delta(text):
        pass

    async def on_done():
        pass

    task = asyncio.create_task(bridge_agent_output("s1", broker, on_delta, on_done))
    await asyncio.sleep(0)  # let it reach `await queue.get()`
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert broker.unsubscribed_queues == [broker.queue]
