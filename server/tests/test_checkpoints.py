"""The pure cut planner: where a live session's messages are sealed into episodes."""
from datetime import UTC, datetime, timedelta

from memvine_server.checkpoints import Event, plan_cuts

T0 = datetime(2026, 10, 26, 9, 0, tzinfo=UTC)


def ev(seq, role, chars, minute=0):
    return Event(seq, role, "x" * chars, T0 + timedelta(minutes=minute))


def plan(events, *, now_min=1, final=False, soft=1000, max_tokens=10_000, max_age=10):
    return plan_cuts(events, now=T0 + timedelta(minutes=now_min), final=final, soft_chars=soft,
                     max_tokens=max_tokens, max_age=timedelta(minutes=max_age))


def test_threshold_cuts_at_the_next_completed_exchange_not_mid_exchange():
    # user/assistant pairs of 300 chars each: 600 per exchange; the last message is an unanswered question.
    events = [ev(i + 1, "user" if i % 2 == 0 else "assistant", 300) for i in range(7)]
    # 1,000 chars is crossed inside exchange 2 (at message 4); the cut waits for the reply to complete it.
    assert plan(events) == [(0, 3)]


def test_a_question_is_never_separated_from_its_answer():
    events = [ev(1, "user", 900), ev(2, "user", 900), ev(3, "assistant", 100), ev(4, "user", 10)]
    # Over the threshold after two user messages, but the first completed exchange ends at 3.
    assert plan(events) == [(0, 2)]


def test_nothing_is_sealed_below_threshold_until_the_oldest_message_ages():
    events = [ev(1, "user", 100, 0), ev(2, "assistant", 100, 1), ev(3, "user", 50, 9)]
    assert plan(events, now_min=9) == []
    # Ten minutes after the OLDEST message, even though a message arrived a minute ago.
    assert plan(events, now_min=10) == [(0, 1)]


def test_session_end_flushes_everything():
    events = [ev(1, "user", 100), ev(2, "assistant", 100), ev(3, "user", 50)]
    assert plan(events, final=True) == [(0, 2)]
    big = [ev(i + 1, "user" if i % 2 == 0 else "assistant", 600) for i in range(6)]
    assert plan(big, final=True) == [(0, 1), (2, 3), (4, 5)], "a long tail is flushed in threshold-sized pieces"


def test_token_cap_bounds_one_extraction():
    events = [ev(1, "user", 200), ev(2, "assistant", 3000), ev(3, "user", 200), ev(4, "assistant", 3000), ev(5, "user", 10)]
    # Cap ~1,000 tokens (3,500 chars): the second exchange would exceed it, so the cut falls after the first.
    assert plan(events, soft=100_000, max_tokens=1000) == [(0, 1)]
    huge = [ev(1, "user", 50_000), ev(2, "assistant", 100), ev(3, "user", 10)]
    # The oversized message is sealed alone; the small exchange after it waits for the threshold or its age.
    assert plan(huge, soft=100_000, max_tokens=1000) == [(0, 0)]
    assert plan(huge, soft=100_000, max_tokens=1000, final=True) == [(0, 0), (1, 2)]
