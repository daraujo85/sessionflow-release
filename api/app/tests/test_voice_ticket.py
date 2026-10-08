import time
from app.voice.ticket import new_ticket, consume_ticket


def test_ticket_is_single_use():
    store: dict = {}
    ticket = new_ticket(store, user_id="u1", ttl_seconds=30)
    assert consume_ticket(store, ticket) == "u1"
    assert consume_ticket(store, ticket) is None  # already consumed


def test_ticket_expires():
    store: dict = {}
    ticket = new_ticket(store, user_id="u1", ttl_seconds=0)
    time.sleep(0.01)
    assert consume_ticket(store, ticket) is None


def test_new_ticket_sweeps_abandoned_expired_tickets():
    """Important #10: an issued-but-never-consumed ticket must not sit in
    `store` forever — the next `new_ticket` call sweeps expired entries."""
    store: dict = {}
    abandoned = new_ticket(store, user_id="u1", ttl_seconds=0)
    time.sleep(0.01)
    assert abandoned in store  # not swept until the next new_ticket call

    new_ticket(store, user_id="u2", ttl_seconds=30)

    assert abandoned not in store  # swept as a side effect
