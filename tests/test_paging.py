"""messages list returns the requested end of the time window, and its cursors page through it without gaps."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from click.testing import Result

from .conftest import FakeChannelHistory, FakeWebClient

RunCli = Callable[..., Result]

CHANNEL = "C0123456789"
WINDOW_START = 1705276800  # 2024-01-15T00:00:00Z
SINCE = "2024-01-15T00:00:00"
UNTIL = "2024-01-15T07:30:00"  # WINDOW_START + 450 minutes
WINDOW_END = WINDOW_START + 450 * 60


def ts(seconds: float, micros: int = 100) -> str:
    return f"{int(seconds)}.{micros:06d}"


def window_channel() -> list[str]:
    """450 messages inside the window, one a minute, and 50 on each side of it."""
    before = [ts(WINDOW_START - 3600 - i * 60) for i in range(50)]
    inside = [ts(WINDOW_START + i * 60) for i in range(450)]
    after = [ts(WINDOW_END + i * 60) for i in range(50)]
    return before + inside + after


def list_json(run_cli: RunCli, *args: str) -> dict[str, Any]:
    result = run_cli("messages", "list", CHANNEL, *args, "--json")
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def window_args(page: dict[str, Any]) -> list[str]:
    """The --since / --until the JSON envelope carries, to follow a cursor inside the window."""
    args: list[str] = []
    if page["since"]:
        args += ["--since", page["since"]]
    if page["until"]:
        args += ["--until", page["until"]]
    return args


def history_calls(fake: FakeWebClient) -> list[dict[str, Any]]:
    return fake.calls_to("conversations.history")


def test_head_pages_through_the_window_oldest_first(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    fake_slack.handle("conversations.history", FakeChannelHistory(window_channel()))
    inside = [ts(WINDOW_START + i * 60) for i in range(450)]

    page1 = list_json(run_cli, "--head", "200", "--since", SINCE, "--until", UNTIL)
    assert [m["ts"] for m in page1["messages"]] == inside[:200]
    assert (page1["has_more_before"], page1["has_more_after"]) == (False, True)
    assert page1["next_before_ts"] is None
    assert (page1["since"], page1["until"]) == ("2024-01-15T00:00:00Z", "2024-01-15T07:30:00Z")

    page2 = list_json(run_cli, "--after", page1["next_after_ts"], "--head", "200", *window_args(page1))
    assert [m["ts"] for m in page2["messages"]] == inside[200:400]
    assert (page2["has_more_before"], page2["has_more_after"]) == (True, True)

    page3 = list_json(run_cli, "--after", page2["next_after_ts"], "--head", "200", *window_args(page2))
    assert [m["ts"] for m in page3["messages"]] == inside[400:]
    assert (page3["has_more_before"], page3["has_more_after"]) == (True, False)
    assert page3["next_after_ts"] is None

    # Head never sends latest, so Slack walks forward from the lower bound
    assert all("latest" not in call for call in history_calls(fake_slack))


def test_tail_pages_through_the_window_newest_first(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    fake_slack.handle("conversations.history", FakeChannelHistory(window_channel()))
    inside = [ts(WINDOW_START + i * 60) for i in range(450)]

    page1 = list_json(run_cli, "--tail", "200", "--since", SINCE, "--until", UNTIL)
    assert [m["ts"] for m in page1["messages"]] == inside[250:]
    assert (page1["has_more_before"], page1["has_more_after"]) == (True, False)

    page2 = list_json(run_cli, "--before", page1["next_before_ts"], "--tail", "200", *window_args(page1))
    assert [m["ts"] for m in page2["messages"]] == inside[50:250]
    assert (page2["has_more_before"], page2["has_more_after"]) == (True, True)

    page3 = list_json(run_cli, "--before", page2["next_before_ts"], "--tail", "200", *window_args(page2))
    assert [m["ts"] for m in page3["messages"]] == inside[:50]
    assert (page3["has_more_before"], page3["has_more_after"]) == (False, True)

    # Tail always sends latest, so Slack walks backward from the upper bound
    assert all("latest" in call for call in history_calls(fake_slack))


def test_tail_with_only_a_lower_bound_returns_the_newest_messages(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    fake_slack.handle("conversations.history", FakeChannelHistory(window_channel()))
    after_window = [ts(WINDOW_END + i * 60) for i in range(50)]

    page = list_json(run_cli, "--tail", "5", "--since", SINCE)

    assert [m["ts"] for m in page["messages"]] == after_window[-5:]
    assert (page["has_more_before"], page["has_more_after"]) == (True, False)
    assert page["until"] is None


def test_today_with_the_default_tail_returns_the_newest_of_today(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    # 30 messages today, one a second after midnight, and 40 the day before
    today = [ts((midnight + timedelta(seconds=1 + i)).timestamp()) for i in range(30)]
    yesterday = [ts((midnight - timedelta(hours=1, seconds=i)).timestamp()) for i in range(40)]
    fake_slack.handle("conversations.history", FakeChannelHistory(sorted(yesterday) + today))

    page = list_json(run_cli, "--today")

    assert [m["ts"] for m in page["messages"]] == today[-25:]
    assert (page["has_more_before"], page["has_more_after"]) == (True, False)


def test_has_more_before_ignores_messages_outside_the_window(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    now = datetime.now(UTC)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today = [ts((midnight + timedelta(seconds=1 + i)).timestamp()) for i in range(10)]
    yesterday = [ts((midnight - timedelta(hours=1, seconds=i)).timestamp()) for i in range(40)]
    fake_slack.handle("conversations.history", FakeChannelHistory(sorted(yesterday) + today))

    page = list_json(run_cli, "--today")

    assert [m["ts"] for m in page["messages"]] == today
    assert (page["has_more_before"], page["has_more_after"]) == (False, False)


def test_head_without_bounds_returns_the_start_of_the_channel(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    channel = [ts(WINDOW_START + i * 60) for i in range(60)]
    fake_slack.handle("conversations.history", FakeChannelHistory(channel))

    page = list_json(run_cli, "--head", "10")

    assert [m["ts"] for m in page["messages"]] == channel[:10]
    assert (page["has_more_before"], page["has_more_after"]) == (False, True)
    # Slack treats oldest=0 as no bound and walks backward; 1 is the channel start
    assert history_calls(fake_slack)[0]["oldest"] == "1.000000"


def test_tail_without_bounds_returns_the_newest_messages(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    channel = [ts(WINDOW_START + i * 60) for i in range(60)]
    fake_slack.handle("conversations.history", FakeChannelHistory(channel))

    page = list_json(run_cli, "--tail", "10")

    assert [m["ts"] for m in page["messages"]] == channel[-10:]
    assert (page["has_more_before"], page["has_more_after"]) == (True, False)


def test_head_with_only_an_upper_bound_walks_from_the_channel_start(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    fake_slack.handle("conversations.history", FakeChannelHistory(window_channel()))
    before_window = sorted(ts(WINDOW_START - 3600 - i * 60) for i in range(50))

    page = list_json(run_cli, "--head", "60", "--until", SINCE)

    assert [m["ts"] for m in page["messages"]] == before_window
    assert (page["has_more_before"], page["has_more_after"]) == (False, False)


def test_bounds_since_inclusive_until_and_cursors_exclusive(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    at_since = ts(WINDOW_START, 0)
    at_until = ts(WINDOW_END, 0)
    middle = [ts(WINDOW_START + 60 * i, 0) for i in range(1, 4)]
    fake_slack.handle("conversations.history", FakeChannelHistory([at_since, *middle, at_until]))

    for direction in ("--head", "--tail"):
        page = list_json(run_cli, direction, "50", "--since", SINCE, "--until", UNTIL)
        assert [m["ts"] for m in page["messages"]] == [at_since, *middle], direction

    after = list_json(run_cli, "--after", at_since, "--since", SINCE, "--until", UNTIL)
    assert [m["ts"] for m in after["messages"]] == middle
    assert after["has_more_before"] is True  # the cursor message itself is inside the window

    before = list_json(run_cli, "--before", middle[-1], "--since", SINCE, "--until", UNTIL)
    assert [m["ts"] for m in before["messages"]] == [at_since, *middle[:-1]]
    assert before["has_more_after"] is True


def test_cursor_outside_the_window_does_not_claim_more(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    fake_slack.handle("conversations.history", FakeChannelHistory(window_channel()))

    # --after a message older than --since: nothing of the window lies before the slice
    page = list_json(run_cli, "--after", ts(WINDOW_START - 3600), "--head", "5", "--since", SINCE, "--until", UNTIL)

    assert page["messages"][0]["ts"] == ts(WINDOW_START)
    assert page["has_more_before"] is False


def test_text_footer_repeats_the_window(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    fake_slack.handle("conversations.history", FakeChannelHistory(window_channel()))

    result = run_cli("messages", "list", CHANNEL, "--head", "200", "--since", SINCE, "--until", UNTIL)

    assert result.exit_code == 0
    last_shown = ts(WINDOW_START + 199 * 60)
    assert f"[newer: --after {last_shown} --since 2024-01-15T00:00:00Z --until 2024-01-15T07:30:00Z]" in result.stdout


class RepeatingHistory:
    """A misbehaving conversations.history that serves the same page and the same cursor on every call."""

    def __init__(self, timestamps: list[str]) -> None:
        self.page = [{"ts": t, "text": f"message {t}", "type": "message"} for t in sorted(timestamps, reverse=True)]

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        return {"messages": list(self.page), "has_more": True, "response_metadata": {"next_cursor": "same"}}


def test_a_repeated_page_ends_the_walk_without_duplicates(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    channel = [ts(WINDOW_START + i * 60) for i in range(3)]
    fake_slack.handle("conversations.history", RepeatingHistory(channel))

    for direction in ("--head", "--tail"):
        page = list_json(run_cli, direction, "50")
        assert [m["ts"] for m in page["messages"]] == channel, direction

    # Each walk stops after the page repeats: the first call, then one call that adds nothing new
    assert len(history_calls(fake_slack)) == 4


def test_empty_pages_with_has_more_end_the_walk(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    fake_slack.handle("conversations.history", RepeatingHistory([]))

    page = list_json(run_cli, "--tail", "50")

    assert page["messages"] == []
    assert len(history_calls(fake_slack)) == 1


def test_a_repeated_cursor_ends_the_walk(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    pages = iter([[ts(WINDOW_START + 120)], [ts(WINDOW_START + 60)], [ts(WINDOW_START)]])

    def history(**kwargs: Any) -> dict[str, Any]:
        page = [{"ts": t, "text": "m", "type": "message"} for t in next(pages)]
        return {"messages": page, "has_more": True, "response_metadata": {"next_cursor": "same"}}

    fake_slack.handle("conversations.history", history)

    page = list_json(run_cli, "--tail", "50")

    # The second page still counts; the walk stops before following "same" a second time
    assert [m["ts"] for m in page["messages"]] == [ts(WINDOW_START + 60), ts(WINDOW_START + 120)]
    assert len(history_calls(fake_slack)) == 2
