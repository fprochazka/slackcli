"""The Slack source of `slack watch`: its calls, its errors and its rate-limit dimensions, against a fake WebClient."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from cli_watch_kit.budget import Budget
from cli_watch_kit.daemon.cache import ByteBoundedCache
from cli_watch_kit.source import PollContext, PollItem, PollOutcome, RateLimited, Target, Transient, Unauthorized
from click.testing import Result
from slack_sdk.errors import SlackApiError

from slackcli.watch import cli as watch_cli
from slackcli.watch import source as source_module
from slackcli.watch.app import APP
from slackcli.watch.diff import Snapshot
from slackcli.watch.source import DIMENSIONS, MAX_PAGES, SlackSource

from .conftest import FakeChannelHistory, FakeWebClient, build_slack_api_error

CHANNEL = "C0123456789"
THREAD = f"{CHANNEL}/1700000000.000100"
TOKEN = "xoxp-test-token"
AUTH = {"team_id": "T0123456789", "user_id": "U0000000001", "url": "https://example.slack.com/"}
ALL_KINDS = frozenset({"message_posted", "message_edited", "message_deleted", "messages_skipped", "channel_event"})


def source_with(fake: FakeWebClient, clock: Callable[[], float] = time.time) -> SlackSource:
    fake.on("auth.test", AUTH)
    return SlackSource(client_factory=lambda token: fake, clock=clock)


def poll(
    source: SlackSource,
    target: Target,
    snapshot: Snapshot | None = None,
    options: tuple[Mapping[str, Any], ...] = (),
) -> PollOutcome[Snapshot]:
    async def run() -> PollOutcome[Snapshot]:
        params = {"token": TOKEN}
        account = await source.account(target, params)
        ctx = PollContext(account, params, Budget(DIMENSIONS), ByteBoundedCache())
        item = PollItem(target, snapshot, ALL_KINDS, options=options)
        return (await source.poll([item], ctx))[item.key]

    return asyncio.run(run())


def rate_limited(method: str) -> SlackApiError:
    return build_slack_api_error("ratelimited", method=method, status_code=429, headers={"Retry-After": "30"})


def test_the_account_is_the_team_and_user_of_the_token() -> None:
    source = source_with(FakeWebClient())

    assert asyncio.run(source.account(Target("channel", CHANNEL), {"token": TOKEN})) == "T0123456789:U0000000001"


def test_a_refused_token_is_unauthorized() -> None:
    fake = FakeWebClient().on("auth.test", build_slack_api_error("invalid_auth", method="auth.test"))
    source = SlackSource(client_factory=lambda token: fake)

    with pytest.raises(Unauthorized):
        asyncio.run(source.account(Target("channel", CHANNEL), {"token": TOKEN}))


@pytest.mark.parametrize(
    ("kind", "target_id", "method", "options", "dimension"),
    [
        ("channel", CHANNEL, "conversations.history", (), "history"),
        ("thread", THREAD, "conversations.replies", (), "replies"),
        ("channel", CHANNEL, "conversations.history", ({"rate_limit": "unlisted"},), "history_unlisted"),
        ("thread", THREAD, "conversations.replies", ({"rate_limit": "unlisted"},), "replies_unlisted"),
    ],
)
def test_a_429_blocks_only_the_dimension_of_its_method(
    kind: str, target_id: str, method: str, options: tuple[Mapping[str, Any], ...], dimension: str
) -> None:
    fake = FakeWebClient().on(method, rate_limited(method))
    source = source_with(fake)
    target = Target(kind, target_id)

    outcome = poll(source, target, options=options)

    assert isinstance(outcome.error, RateLimited)
    assert outcome.error.dimension == dimension
    assert outcome.error.retry_after == 30
    assert source.cost([PollItem(target, None, ALL_KINDS, options=options)]) == {dimension: 1.0}
    assert dimension in DIMENSIONS


def test_every_dimension_is_paced_below_slacks_limit() -> None:
    assert DIMENSIONS["history"].per_minute == 25
    assert DIMENSIONS["replies"].per_minute == 25
    assert DIMENSIONS["history_unlisted"].per_minute == 1


@pytest.mark.parametrize(
    ("error", "reason"),
    [("not_in_channel", "no_access"), ("channel_not_found", "not_found"), ("thread_not_found", "not_found")],
)
def test_a_channel_out_of_reach_ends_the_watch(error: str, reason: str) -> None:
    fake = FakeWebClient().on("conversations.replies", build_slack_api_error(error, method="conversations.replies"))

    outcome = poll(source_with(fake), Target("thread", THREAD), {"latest": "1700000000.000100", "messages": {}})

    assert outcome.ended == reason


def test_a_server_error_is_transient() -> None:
    error = build_slack_api_error("internal_error", method="conversations.history", status_code=500)
    fake = FakeWebClient().on("conversations.history", error)

    assert isinstance(poll(source_with(fake), Target("channel", CHANNEL)).error, Transient)


def test_a_channel_poll_reads_forward_from_the_cursor_and_the_recheck_window() -> None:
    now = 1700001000.0
    history = FakeChannelHistory(["1700000500.000100", "1700000900.000100", "1700000950.000100"])
    fake = FakeWebClient().handle("conversations.history", history)
    source = source_with(fake, clock=lambda: now)
    previous: Snapshot = {"latest": "1700000900.000100", "messages": {}}

    outcome = poll(source, Target("channel", CHANNEL), previous)

    call = fake.calls_to("conversations.history")[0]
    assert call["oldest"] == "1700000400.000000"  # now minus the 10 minute window, older than the cursor
    assert call["inclusive"] is True
    assert "latest" not in call
    assert [e.kind for e in outcome.events] == ["message_posted"]
    assert outcome.events[0].data["message"]["ts"] == "1700000950.000100"
    assert outcome.snapshot is not None
    assert list(outcome.snapshot["messages"]) == ["1700000500.000100", "1700000900.000100", "1700000950.000100"]


def test_a_zero_recheck_window_reads_only_past_the_cursor() -> None:
    now = 1700001000.0
    history = FakeChannelHistory(["1700000900.000100", "1700000950.000100"])
    fake = FakeWebClient().handle("conversations.history", history)
    source = source_with(fake, clock=lambda: now)

    poll(source, Target("channel", CHANNEL), {"latest": "1700000900.000100", "messages": {}}, ({"recheck_window": 0},))

    assert fake.calls_to("conversations.history")[0]["oldest"] == "1700000900.000100"


def test_a_long_catch_up_stops_paging_and_reports_one_messages_skipped() -> None:
    now = 1800000000.0
    timestamps = [f"{1700000000 + n}.000100" for n in range(MAX_PAGES * 200 + 50)]
    fake = FakeWebClient().handle("conversations.history", FakeChannelHistory(timestamps))
    source = source_with(fake, clock=lambda: now)

    outcome = poll(source, Target("channel", CHANNEL), {"latest": "1699999999.000100", "messages": {}})

    calls = fake.calls_to("conversations.history")
    assert len(calls) == MAX_PAGES + 1  # the pages, then one call for the newest ts
    assert [e.kind for e in outcome.events] == ["messages_skipped"]
    data = outcome.events[0].data
    assert data["truncated"] is True
    assert data["count"] == MAX_PAGES * 200
    assert data["newest_ts"] == timestamps[-1]
    assert outcome.snapshot is not None and outcome.snapshot["latest"] == timestamps[-1]


def test_a_thread_poll_reads_the_whole_thread() -> None:
    parent = {"ts": "1700000000.000100", "thread_ts": "1700000000.000100", "user": "U0123456789", "text": "q"}
    reply = {"ts": "1700000100.000100", "thread_ts": "1700000000.000100", "user": "U0123456789", "text": "a"}
    fake = FakeWebClient().on("conversations.replies", {"messages": [parent, reply], "has_more": False})

    outcome = poll(source_with(fake), Target("thread", THREAD), {"latest": "1700000000.000100", "messages": {}})

    assert fake.calls_to("conversations.replies")[0] == {"channel": CHANNEL, "ts": "1700000000.000100", "limit": 1000}
    assert [e.kind for e in outcome.events] == ["message_posted"]
    assert outcome.events[0].data["message"]["ts"] == "1700000100.000100"


# --- end to end, in process (--no-daemon) -------------------------------------------------------------------


def test_a_channel_watch_starts_silent_and_reports_a_new_message(
    run_cli: Callable[..., Result], fake_slack: FakeWebClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    old = f"{now - 120:.6f}"
    history = FakeChannelHistory([old])
    calls = 0

    def answer(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            # Posted between the baseline and the next poll, mentioning someone
            history.messages.append(
                {"type": "message", "ts": f"{now + 1:.6f}", "user": "U0123456789", "text": "hi <@U0987654321>"}
            )
        return history(**kwargs)

    users = {"U0123456789": "alice", "U0987654321": "bob"}
    fake_slack.on("auth.test", AUTH, AUTH)
    fake_slack.handle("conversations.history", answer)
    fake_slack.handle("users.info", lambda user: {"user": {"id": user, "name": users[user], "profile": {}}})
    source = SlackSource(client_factory=lambda token: fake_slack)
    monkeypatch.setattr(watch_cli, "APP", APP.with_overrides(source=source, client_poll_ms=50))
    monkeypatch.setattr(source_module, "INTERVAL", 0.2)

    result = run_cli(
        "watch",
        "channel",
        CHANNEL,
        "--no-daemon",
        "--events-stream",
        "stdout",
        "--base-dir",
        str(tmp_path),
        "--until",
        "message_posted",
        "--timeout",
        "20s",
    )

    assert result.exit_code == 0, result.stderr
    header = result.stderr.splitlines()
    assert f"slack watch: channel: {CHANNEL}" in header
    assert "slack watch: org: testorg (https://example.slack.com/)" in header
    assert "slack watch: own messages: not reported (--include-own reports them)" in header
    assert any(line.startswith("slack watch: ignored: channel_event") for line in header)
    assert f"slack watch: dir: {tmp_path / 'testorg' / 'channels' / CHANNEL}" in header
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [e["kind"] for e in events] == ["watch_started", "message_posted", "watch_ended"]
    posted = events[1]["data"]
    assert posted["channel"] == CHANNEL
    assert posted["message"]["user_name"] == "alice"
    assert posted["message"]["text"] == "hi @bob"
    assert (
        posted["permalink"]
        == f"https://example.slack.com/archives/{CHANNEL}/p{posted['message']['ts'].replace('.', '')}"
    )
    assert (tmp_path / "testorg" / "channels" / CHANNEL / "state.json").exists()
