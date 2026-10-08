import pytest

from app.voice.session_router import route_global, route_session


class FakeSessionsRepo:
    async def list_active(self):
        return [{"name": "pagamentos"}, {"name": "dashboard"}, {"name": "testes"}]


@pytest.mark.asyncio
async def test_lists_active_sessions_by_name():
    text = await route_global("quais sessões estão ativas?", FakeSessionsRepo())
    assert "pagamentos" in text
    assert "dashboard" in text
    assert "testes" in text


@pytest.mark.asyncio
async def test_no_active_sessions_message():
    class Empty(FakeSessionsRepo):
        async def list_active(self):
            return []

    text = await route_global("quais sessões estão ativas?", Empty())
    assert "nenhuma" in text.lower()


@pytest.mark.asyncio
async def test_route_session_publishes_input_command_with_worker_payload_shape():
    """Payload keys must match `command_consumer.py`'s `_handle_input`
    contract (`name` + `text`), not an invented shape — otherwise the Worker
    raises `CommandError("input requer 'name'")`."""
    calls = []

    async def fake_publish_command_fn(**kwargs):
        calls.append(kwargs)
        return "cmd-1"

    await route_session("oi, tudo bem?", "sess-1", fake_publish_command_fn)

    assert calls == [
        {
            "type": "input",
            "payload": {"name": "sess-1", "text": "oi, tudo bem?"},
            "host_id": None,
        }
    ]


@pytest.mark.asyncio
async def test_route_session_forwards_host_id():
    """Critical #2: `host_id` must reach `publish_command_fn` so the direct
    exchange routes to the worker's own binding key."""
    calls = []

    async def fake_publish_command_fn(**kwargs):
        calls.append(kwargs)
        return "cmd-1"

    await route_session("oi", "sess-1", fake_publish_command_fn, host_id="host-1")

    assert calls == [
        {
            "type": "input",
            "payload": {"name": "sess-1", "text": "oi"},
            "host_id": "host-1",
        }
    ]
