"""The pure diff of `slack watch`: Slack rows against the previous snapshot."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from slackcli.watch.diff import CATCH_UP_CAP, DiffResult, Identity, Snapshot, diff_rows, gap_diff, relevant_rows

CHANNEL = "C0123456789"
ME = Identity(user_id="U0000000001")
ALICE = "U0123456789"


def row(ts: str, text: str = "hello", user: str | None = ALICE, **extra: Any) -> dict[str, Any]:
    data: dict[str, Any] = {"type": "message", "ts": ts, "text": text}
    if user:
        data["user"] = user
    data.update(extra)
    return data


def channel_poll(previous: Snapshot | None, rows: list[dict[str, Any]], **kwargs: Any) -> DiffResult:
    options: dict[str, Any] = {
        "mode": "channel",
        "channel": CHANNEL,
        "identity": ME,
        "keep_from": Decimal("1700000000"),
        "checked_from": Decimal("1700000000"),
        "checked_until": None,
    }
    options.update(kwargs)
    return diff_rows(previous, relevant_rows(rows, "channel"), **options)


def kinds(result: DiffResult) -> list[str]:
    return [e.kind for e in result.events]


def baseline(*rows: dict[str, Any]) -> Snapshot:
    return channel_poll(None, list(rows)).snapshot


def test_the_baseline_reports_nothing_and_remembers_the_newest_ts() -> None:
    result = channel_poll(None, [row("1700000010.000100"), row("1700000020.000100")])

    assert result.events == []
    assert result.snapshot["latest"] == "1700000020.000100"
    assert list(result.snapshot["messages"]) == ["1700000010.000100", "1700000020.000100"]


def test_a_new_message_is_posted_with_the_raw_row() -> None:
    previous = baseline(row("1700000010.000100"))
    new = row("1700000030.000100", "deploy done")

    result = channel_poll(previous, [row("1700000010.000100"), new])

    assert kinds(result) == ["message_posted"]
    data = result.events[0].data
    assert data["message"] == new
    assert data["channel"] == CHANNEL
    assert data["own"] is False
    assert data["broadcast"] is False
    assert result.snapshot["latest"] == "1700000030.000100"


def test_own_messages_are_marked_for_the_watcher_to_drop() -> None:
    previous = baseline(row("1700000010.000100"))

    result = channel_poll(previous, [row("1700000010.000100"), row("1700000030.000100", user=ME.user_id)])

    assert result.events[0].data["own"] is True


def test_a_bot_message_is_an_ordinary_post() -> None:
    previous = baseline(row("1700000010.000100"))
    webhook = row("1700000030.000100", "Build #42 passed", user=None, subtype="bot_message", bot_id="B0123456789")

    result = channel_poll(previous, [row("1700000010.000100"), webhook])

    assert kinds(result) == ["message_posted"]
    assert result.events[0].data["message"]["bot_id"] == "B0123456789"


def test_an_edit_inside_the_window_is_reported() -> None:
    previous = baseline(row("1700000010.000100", "helo"))
    edited = row("1700000010.000100", "hello", edited={"user": ALICE, "ts": "1700000040.000000"})

    result = channel_poll(previous, [edited])

    assert kinds(result) == ["message_edited"]
    assert result.events[0].data["edited"] == {"ts": "1700000040.000000", "user": ALICE}
    assert result.snapshot["messages"]["1700000010.000100"]["edited"] == "1700000040.000000"


def test_a_deletion_inside_the_window_carries_the_last_known_text() -> None:
    previous = baseline(row("1700000010.000100", "oops, wrong channel"), row("1700000020.000100"))

    result = channel_poll(previous, [row("1700000020.000100")])

    assert kinds(result) == ["message_deleted"]
    data = result.events[0].data
    assert data["ts"] == "1700000010.000100"
    assert data["text"] == "oops, wrong channel"
    assert data["user_id"] == ALICE


def test_a_message_that_left_the_window_is_not_deleted() -> None:
    previous = baseline(row("1700000010.000100"), row("1700000020.000100"))

    result = channel_poll(previous, [row("1700000020.000100")], checked_from=Decimal("1700000015"))

    assert result.events == []
    assert list(result.snapshot["messages"]) == ["1700000020.000100"]


def test_a_deleted_thread_parent_with_replies_turns_into_a_tombstone() -> None:
    previous = baseline(row("1700000010.000100", "the question", reply_count=2))
    tombstone = row("1700000010.000100", "This message was deleted.", user=None, subtype="tombstone")

    result = channel_poll(previous, [tombstone])

    assert kinds(result) == ["message_deleted"]
    assert result.events[0].data["text"] == "the question"


def test_thread_replies_are_not_channel_messages_but_broadcasts_are() -> None:
    previous = baseline(row("1700000010.000100"))
    reply = row("1700000030.000100", "in the thread", thread_ts="1700000010.000100")
    broadcast = row(
        "1700000040.000100", "also sent to the channel", thread_ts="1700000010.000100", subtype="thread_broadcast"
    )

    result = channel_poll(previous, [row("1700000010.000100"), reply, broadcast])

    assert kinds(result) == ["message_posted"]
    assert result.events[0].data["message"]["ts"] == "1700000040.000100"
    assert result.events[0].data["broadcast"] is True


def test_reply_count_changes_on_a_root_are_not_an_edit() -> None:
    previous = baseline(row("1700000010.000100"))

    result = channel_poll(previous, [row("1700000010.000100", reply_count=3, latest_reply="1700000050.000100")])

    assert result.events == []


def test_joins_and_topic_changes_are_channel_events() -> None:
    previous = baseline(row("1700000010.000100"))
    join = row("1700000030.000100", "<@U0123456789> has joined the channel", subtype="channel_join")
    topic = row("1700000040.000100", "set the channel topic: launch", subtype="channel_topic")

    result = channel_poll(previous, [row("1700000010.000100"), join, topic])

    assert kinds(result) == ["channel_event", "channel_event"]
    assert [e.data["subtype"] for e in result.events] == ["channel_join", "channel_topic"]
    assert "1700000030.000100" not in result.snapshot["messages"]


def test_an_archive_row_ends_the_watch() -> None:
    previous = baseline(row("1700000010.000100"))
    archive = row("1700000030.000100", "archived the channel", subtype="channel_archive")

    assert channel_poll(previous, [row("1700000010.000100"), archive]).ended == "archived"


def test_a_channel_archived_before_the_watch_ends_it_at_the_baseline() -> None:
    archive = row("1700000030.000100", "archived the channel", subtype="channel_archive")

    assert channel_poll(None, [archive]).ended == "archived"


def test_up_to_the_cap_new_messages_are_reported_one_by_one() -> None:
    previous = baseline(row("1700000010.000100"))
    new = [row(f"17000001{n:02d}.000100") for n in range(CATCH_UP_CAP)]

    assert kinds(channel_poll(previous, [row("1700000010.000100"), *new])) == ["message_posted"] * CATCH_UP_CAP


def test_more_than_the_cap_become_one_messages_skipped() -> None:
    previous = baseline(row("1700000010.000100"))
    new = [row(f"17000001{n:02d}.000100") for n in range(CATCH_UP_CAP + 1)]

    result = channel_poll(previous, [row("1700000010.000100"), *new])

    assert kinds(result) == ["messages_skipped"]
    assert result.events[0].data == {
        "channel": CHANNEL,
        "count": CATCH_UP_CAP + 1,
        "truncated": False,
        "oldest_ts": "1700000100.000100",
        "newest_ts": "1700000150.000100",
    }
    assert result.snapshot["latest"] == "1700000150.000100"


def test_a_catch_up_that_stopped_paging_jumps_to_the_newest_message() -> None:
    previous = baseline(row("1700000010.000100"))
    fetched = [row("1700000010.000100"), row("1700000100.000100"), row("1700000101.000100")]

    result = channel_poll(
        previous, fetched, newest_unfetched="1700009999.000100", checked_until=Decimal("1700000101.0001")
    )

    assert kinds(result) == ["messages_skipped"]
    assert result.events[0].data["truncated"] is True
    assert result.events[0].data["newest_ts"] == "1700009999.000100"
    assert result.snapshot["latest"] == "1700009999.000100"


def test_a_thread_watch_reports_replies_and_drops_the_parent() -> None:
    parent = row("1700000010.000100", "the question", reply_count=1, thread_ts="1700000010.000100")
    first = row("1700000020.000100", "an answer", thread_ts="1700000010.000100")
    options: dict[str, Any] = {
        "mode": "thread",
        "channel": CHANNEL,
        "identity": ME,
        "keep_from": None,
        "checked_from": None,
        "checked_until": None,
        "thread_ts": "1700000010.000100",
        "baseline_latest": "1700000010.000100",
    }
    start = diff_rows(None, relevant_rows([parent, first], "thread", "1700000010.000100"), **options)
    second = row("1700000030.000100", "another one", thread_ts="1700000010.000100", subtype="thread_broadcast")

    result = diff_rows(start.snapshot, relevant_rows([parent, second], "thread", "1700000010.000100"), **options)

    assert sorted(kinds(result)) == ["message_deleted", "message_posted"]
    posted = next(e for e in result.events if e.kind == "message_posted")
    assert posted.data["thread_ts"] == "1700000010.000100"
    assert posted.data["message"]["ts"] == "1700000030.000100"


def test_an_empty_thread_starts_from_the_parent_ts() -> None:
    parent = row("1700000010.000100", "the question", thread_ts="1700000010.000100")
    result = diff_rows(
        None,
        relevant_rows([parent], "thread", "1700000010.000100"),
        mode="thread",
        channel=CHANNEL,
        identity=ME,
        keep_from=None,
        checked_from=None,
        checked_until=None,
        thread_ts="1700000010.000100",
        baseline_latest="1700000010.000100",
    )

    assert result.snapshot == {"latest": "1700000010.000100", "messages": {}}


def test_the_gap_diff_reports_what_changed_between_two_snapshots() -> None:
    before = baseline(row("1700000010.000100", "first"), row("1700000020.000100", "second"))
    after = baseline(
        row("1700000010.000100", "first!", edited={"ts": "1700000050.000000"}),
        row("1700000060.000100", "third"),
    )

    found = gap_diff(before, after, mode="channel", channel=CHANNEL, thread_ts=None)

    assert [e.kind for e in found] == ["message_edited", "message_deleted", "message_posted"]
    assert all(e.data["gap"] for e in found if e.kind != "message_deleted")


def test_the_gap_diff_reports_messages_outside_the_window_as_skipped() -> None:
    before: Snapshot = {"latest": "1700000010.000100", "messages": {}}
    after: Snapshot = {"latest": "1700000090.000100", "messages": {}}

    found = gap_diff(before, after, mode="channel", channel=CHANNEL, thread_ts=None)

    assert [e.kind for e in found] == ["messages_skipped"]
    assert found[0].data["oldest_ts"] == "1700000010.000100"
    assert found[0].data["newest_ts"] == "1700000090.000100"
