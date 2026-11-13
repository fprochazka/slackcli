"""A Slack call that fails part way through a listing fails the command instead of returning partial data."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from click.testing import Result
from slack_sdk.errors import SlackApiError

from slackcli import cli as cli_module
from slackcli.client import SlackCli
from slackcli.errors import EXIT_RATE_LIMITED
from slackcli.users import UserInfo, get_user, is_users_cache_complete, load_all_users_from_cache, save_user_to_cache

from .conftest import FakeWebClient

RunCli = Callable[..., Result]
MakeError = Callable[..., SlackApiError]

ORG = "testorg"


def user_data(user_id: str, name: str) -> dict[str, Any]:
    return {"id": user_id, "name": name, "real_name": name.title(), "profile": {"display_name": name}}


@pytest.fixture
def rate_limited(make_slack_api_error: MakeError) -> Callable[[str], SlackApiError]:
    def build(method: str) -> SlackApiError:
        return make_slack_api_error("ratelimited", method=method, status_code=429, headers={"Retry-After": "30"})

    return build


# --- users list / users search: paging through users.list ---------------------------------------------------


def test_users_list_fails_when_a_later_page_is_rate_limited(
    run_cli: RunCli, fake_slack: FakeWebClient, rate_limited: Callable[[str], SlackApiError]
) -> None:
    fake_slack.on(
        "users.list",
        {"members": [user_data("U0000000001", "alice")], "response_metadata": {"next_cursor": "page2"}},
        rate_limited("users.list"),
    )

    result = run_cli("users", "list", "--json")

    assert result.exit_code == EXIT_RATE_LIMITED
    assert result.stdout == ""
    assert "Retry-After: 30s (method: users.list)" in result.stderr
    assert not is_users_cache_complete(ORG)


def test_partial_users_cache_is_not_used_as_the_full_list(
    run_cli: RunCli, fake_slack: FakeWebClient, rate_limited: Callable[[str], SlackApiError]
) -> None:
    fake_slack.on(
        "users.list",
        {"members": [user_data("U0000000001", "alice")], "response_metadata": {"next_cursor": "page2"}},
        rate_limited("users.list"),
    )
    assert run_cli("users", "list").exit_code == EXIT_RATE_LIMITED
    # The first page is on disk, but it is not the whole workspace
    assert [u.name for u in load_all_users_from_cache(ORG)] == ["alice"]

    fake_slack.on(
        "users.list",
        {"members": [user_data("U0000000001", "alice")], "response_metadata": {"next_cursor": "page2"}},
        {"members": [user_data("U0000000002", "bob")], "response_metadata": {"next_cursor": ""}},
    )
    result = run_cli("users", "list", "--json")

    assert result.exit_code == 0
    assert [u["name"] for u in json.loads(result.stdout)["users"]] == ["alice", "bob"]
    assert len(fake_slack.calls_to("users.list")) == 4
    assert is_users_cache_complete(ORG)

    # Now the cache holds the full list and answers without an API call
    result = run_cli("users", "list", "--json")
    assert result.exit_code == 0
    assert json.loads(result.stdout)["count"] == 2
    assert len(fake_slack.calls_to("users.list")) == 4


def test_users_cached_one_by_one_are_not_used_as_the_full_list(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    fake_slack.on("users.info", {"user": user_data("U0000000001", "alice")})
    assert run_cli("users", "get", "U0000000001").exit_code == 0

    fake_slack.on(
        "users.list",
        {"members": [user_data("U0000000001", "alice"), user_data("U0000000002", "bob")]},
    )
    result = run_cli("users", "search", "bob", "--json")

    assert result.exit_code == 0
    assert [u["name"] for u in json.loads(result.stdout)["users"]] == ["bob"]


def test_users_search_exits_75_on_rate_limit(
    run_cli: RunCli, fake_slack: FakeWebClient, rate_limited: Callable[[str], SlackApiError]
) -> None:
    fake_slack.on("users.list", rate_limited("users.list"))

    result = run_cli("users", "search", "alice", "--json")

    assert result.exit_code == EXIT_RATE_LIMITED
    assert result.stdout == ""


# --- users get / messages send U… / resolve of a user URL: users.info ---------------------------------------


def test_users_get_exits_75_on_rate_limit(
    run_cli: RunCli, fake_slack: FakeWebClient, rate_limited: Callable[[str], SlackApiError]
) -> None:
    fake_slack.on("users.info", rate_limited("users.info"))

    result = run_cli("users", "get", "U0000000001", "--json")

    assert result.exit_code == EXIT_RATE_LIMITED
    assert result.stdout == ""
    assert "Retry-After: 30s (method: users.info)" in result.stderr


def test_users_get_by_name_exits_75_on_rate_limit(
    run_cli: RunCli, fake_slack: FakeWebClient, rate_limited: Callable[[str], SlackApiError]
) -> None:
    fake_slack.on("users.list", rate_limited("users.list"))

    result = run_cli("users", "get", "@alice")

    assert result.exit_code == EXIT_RATE_LIMITED
    assert "User not found" not in result.stderr


def test_users_get_unknown_user_still_says_not_found(
    run_cli: RunCli, fake_slack: FakeWebClient, make_slack_api_error: MakeError
) -> None:
    fake_slack.on("users.info", make_slack_api_error("user_not_found", method="users.info"))

    result = run_cli("users", "get", "U0000000001")

    assert result.exit_code == 1
    assert "User not found: U0000000001" in result.stderr


def test_messages_send_to_user_exits_75_without_posting(
    run_cli: RunCli, fake_slack: FakeWebClient, rate_limited: Callable[[str], SlackApiError]
) -> None:
    fake_slack.on("users.info", rate_limited("users.info"))

    result = run_cli("messages", "send", "U0000000001", "hello")

    assert result.exit_code == EXIT_RATE_LIMITED
    assert "Could not resolve user" not in result.stderr
    assert fake_slack.calls_to("chat.postMessage") == []
    assert fake_slack.calls_to("conversations.open") == []


def test_resolve_user_url_exits_75_on_rate_limit(
    run_cli: RunCli, fake_slack: FakeWebClient, rate_limited: Callable[[str], SlackApiError]
) -> None:
    fake_slack.on("users.info", rate_limited("users.info"))

    result = run_cli("resolve", f"https://{ORG}.slack.com/team/U0000000001", "--json")

    assert result.exit_code == EXIT_RATE_LIMITED
    assert result.stdout == ""


# --- conversations list: group DM members ------------------------------------------------------------------


def test_conversations_refresh_fails_and_caches_nothing_when_group_dm_members_fail(
    run_cli: RunCli,
    fake_slack: FakeWebClient,
    rate_limited: Callable[[str], SlackApiError],
    isolated_cache_dir: Path,
) -> None:
    fake_slack.on(
        "conversations.list",
        {"channels": [{"id": "G0000000001", "name": "mpdm-alice--bob-1", "is_mpim": True}]},
    )
    fake_slack.on("conversations.members", rate_limited("conversations.members"))

    result = run_cli("conversations", "list", "--refresh")

    assert result.exit_code == EXIT_RATE_LIMITED
    assert "Retry-After: 30s (method: conversations.members)" in result.stderr
    assert not any(isolated_cache_dir.rglob("conversations*.json"))


# --- messages list --with-threads --------------------------------------------------------------------------

ROOT_A = {"ts": "1700000100.000000", "text": "root a", "reply_count": 1, "thread_ts": "1700000100.000000"}
ROOT_B = {"ts": "1700000200.000000", "text": "root b", "reply_count": 1, "thread_ts": "1700000200.000000"}
PLAIN = {"ts": "1700000300.000000", "text": "plain"}


def history(*messages: dict[str, Any]) -> dict[str, Any]:
    # conversations.history returns newest first
    return {"messages": [dict(m) for m in reversed(messages)], "has_more": False}


def thread(root: dict[str, Any], reply_text: str) -> dict[str, Any]:
    reply = {"ts": f"{root['ts'][:-1]}1", "text": reply_text, "thread_ts": root["ts"]}
    return {"messages": [dict(root), reply], "has_more": False}


def test_with_threads_rate_limit_marks_the_roots_prints_everything_and_exits_75(
    run_cli: RunCli, fake_slack: FakeWebClient, rate_limited: Callable[[str], SlackApiError]
) -> None:
    fake_slack.on("conversations.history", history(ROOT_A, ROOT_B, PLAIN))
    fake_slack.on("conversations.replies", rate_limited("conversations.replies"))

    result = run_cli("messages", "list", "C0123456789", "--with-threads", "--json")

    assert result.exit_code == EXIT_RATE_LIMITED
    messages = {m["ts"]: m for m in json.loads(result.stdout)["messages"]}
    assert set(messages) == {ROOT_A["ts"], ROOT_B["ts"], PLAIN["ts"]}
    assert messages[ROOT_A["ts"]]["replies_error"] == "ratelimited"
    # The second thread is not tried after the rate limit, and says why its replies are missing
    assert messages[ROOT_B["ts"]]["replies_error"] == "ratelimited"
    assert len(fake_slack.calls_to("conversations.replies")) == 1
    assert "replies_error" not in messages[PLAIN["ts"]]
    assert "Retry-After: 30s (method: conversations.replies)" in result.stderr
    assert f"Replies of thread {ROOT_A['ts']} not loaded" in result.stderr


def test_with_threads_other_error_keeps_loading_and_exits_1(
    run_cli: RunCli, fake_slack: FakeWebClient, make_slack_api_error: MakeError
) -> None:
    fake_slack.on("conversations.history", history(ROOT_A, ROOT_B))
    fake_slack.on(
        "conversations.replies",
        make_slack_api_error("thread_not_found", method="conversations.replies"),
        thread(ROOT_B, "reply b"),
    )

    result = run_cli("messages", "list", "C0123456789", "--with-threads", "--json")

    assert result.exit_code == 1
    messages = {m["ts"]: m for m in json.loads(result.stdout)["messages"]}
    assert messages[ROOT_A["ts"]]["replies_error"] == "thread_not_found"
    assert "replies" not in messages[ROOT_A["ts"]]
    assert [r["text"] for r in messages[ROOT_B["ts"]]["replies"]] == ["reply b"]
    assert "replies_error" not in messages[ROOT_B["ts"]]


def test_with_threads_text_output_marks_the_missing_replies(
    run_cli: RunCli, fake_slack: FakeWebClient, rate_limited: Callable[[str], SlackApiError]
) -> None:
    fake_slack.on("conversations.history", history(ROOT_A, PLAIN))
    fake_slack.on("conversations.replies", rate_limited("conversations.replies"))

    result = run_cli("messages", "list", "C0123456789", "--with-threads")

    assert result.exit_code == EXIT_RATE_LIMITED
    assert "[replies not loaded: ratelimited]" in result.stdout
    assert "root a" in result.stdout
    assert "plain" in result.stdout


def test_with_threads_success_exits_0_without_replies_error(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    fake_slack.on("conversations.history", history(ROOT_A))
    fake_slack.on("conversations.replies", thread(ROOT_A, "reply a"))

    result = run_cli("messages", "list", "C0123456789", "--with-threads", "--json")

    assert result.exit_code == 0
    [root] = json.loads(result.stdout)["messages"]
    assert [r["text"] for r in root["replies"]] == ["reply a"]
    assert "replies_error" not in root


# --- permanent users.info answers: the raw ID is shown, as before ---------------------------------------------


@pytest.mark.parametrize("error_code", ["user_not_found", "user_not_visible"])
def test_messages_list_shows_the_raw_id_of_a_user_slack_will_not_show(
    run_cli: RunCli, fake_slack: FakeWebClient, make_slack_api_error: MakeError, error_code: str
) -> None:
    fake_slack.on("conversations.history", history({"ts": "1700000100.000000", "text": "hi", "user": "U0000000001"}))
    fake_slack.on("users.info", make_slack_api_error(error_code, method="users.info"))

    result = run_cli("messages", "list", "C0123456789", "--json")

    assert result.exit_code == 0
    [message] = json.loads(result.stdout)["messages"]
    assert message["user_id"] == "U0000000001"
    assert message["user_name"] == "U0000000001"


@pytest.mark.parametrize("error_code", ["user_not_found", "user_not_visible"])
def test_get_user_falls_back_to_expired_cache_on_a_permanent_answer(
    fake_slack: FakeWebClient, make_slack_api_error: MakeError, error_code: str
) -> None:
    expired = UserInfo.from_api(user_data("U0000000001", "alice"))
    expired.updated_at = (datetime.now() - timedelta(days=30)).isoformat()
    save_user_to_cache(ORG, expired)
    fake_slack.on("users.info", make_slack_api_error(error_code, method="users.info"))

    user = get_user(SlackCli(org_name=ORG, token="xoxp-test-token"), "U0000000001")

    assert user is not None
    assert user.name == "alice"


def test_get_user_does_not_fall_back_to_expired_cache_on_a_rate_limit(
    fake_slack: FakeWebClient, rate_limited: Callable[[str], SlackApiError]
) -> None:
    expired = UserInfo.from_api(user_data("U0000000001", "alice"))
    expired.updated_at = (datetime.now() - timedelta(days=30)).isoformat()
    save_user_to_cache(ORG, expired)
    fake_slack.on("users.info", rate_limited("users.info"))

    with pytest.raises(SlackApiError):
        get_user(SlackCli(org_name=ORG, token="xoxp-test-token"), "U0000000001")


# --- the real entry point ---------------------------------------------------------------------------------


def test_messages_list_user_lookup_rate_limit_exits_75_through_the_entry_point(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cli_config_path: Path,
    fake_slack: FakeWebClient,
    rate_limited: Callable[[str], SlackApiError],
) -> None:
    fake_slack.on("conversations.history", history({"ts": "1700000100.000000", "text": "hi", "user": "U0000000001"}))
    fake_slack.on("users.info", rate_limited("users.info"))
    monkeypatch.setattr(
        "sys.argv", ["slack", "--config", str(cli_config_path), "messages", "list", "C0123456789", "--json"]
    )

    with pytest.raises(SystemExit) as exc_info:
        cli_module.cli()

    assert exc_info.value.code == EXIT_RATE_LIMITED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Retry-After: 30s (method: users.info)" in captured.err
