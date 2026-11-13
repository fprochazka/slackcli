"""A cache written by an older schema version is treated as expired."""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import Result
from slack_sdk.errors import SlackApiError

from slackcli.client import SlackCli
from slackcli.commands import conversations as conversations_module
from slackcli.commands.conversations import (
    CACHE_NAME,
    is_cache_expired,
    load_conversations_for_lookup,
    load_conversations_from_cache,
    save_conversations_to_cache,
)
from slackcli.models import Conversation
from slackcli.users import (
    UserInfo,
    get_user,
    is_users_cache_complete,
    load_all_users_from_cache,
    load_user_from_cache,
    mark_users_cache_complete,
    save_user_to_cache,
)

from .conftest import FakeWebClient

ORG = "testorg"


def conversation() -> Conversation:
    return Conversation(
        id="C0123456789",
        name="general",
        is_private=False,
        is_channel=True,
        is_group=False,
        is_im=False,
        is_mpim=False,
        is_member=True,
        topic="",
        purpose="",
        num_members=1,
        created=0,
    )


def user_data(user_id: str, name: str) -> dict[str, Any]:
    return {"id": user_id, "name": name, "real_name": name.title(), "profile": {"display_name": name}}


def bump(monkeypatch: pytest.MonkeyPatch, target: str) -> None:
    """Pretend the code now writes the next schema version, so what is cached now is from an older version."""
    module_name, _, attribute = target.rpartition(".")
    current = getattr(importlib.import_module(module_name), attribute)
    monkeypatch.setattr(target, current + 1)


def strip_version(path: Path, *keys: str) -> None:
    """Remove the recorded version from a cache file, as written before versions were checked."""
    data = json.loads(path.read_text())
    holder = data
    for key in keys:
        holder = holder[key]
    del holder["version"]
    path.write_text(json.dumps(data))


# --- conversations cache ---------------------------------------------------------------------------------


def test_conversations_cache_of_the_current_version_is_used() -> None:
    save_conversations_to_cache(ORG, [conversation()])

    cached = load_conversations_from_cache(ORG)

    assert cached is not None
    assert [c.id for c in cached] == ["C0123456789"]
    assert not is_cache_expired(ORG, CACHE_NAME, conversations_module.CONVERSATIONS_CACHE_VERSION)


def test_conversations_cache_of_another_version_is_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    save_conversations_to_cache(ORG, [conversation()])
    bump(monkeypatch, "slackcli.commands.conversations.CONVERSATIONS_CACHE_VERSION")

    assert load_conversations_from_cache(ORG) is None
    assert is_cache_expired(ORG, CACHE_NAME, conversations_module.CONVERSATIONS_CACHE_VERSION)


def test_conversations_cache_without_a_version_counts_as_version_1(
    isolated_cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_conversations_to_cache(ORG, [conversation()])
    strip_version(isolated_cache_dir / ORG / "conversations.json", "metadata")

    monkeypatch.setattr("slackcli.commands.conversations.CONVERSATIONS_CACHE_VERSION", 1)
    assert load_conversations_from_cache(ORG) is not None
    monkeypatch.setattr("slackcli.commands.conversations.CONVERSATIONS_CACHE_VERSION", 2)
    assert load_conversations_from_cache(ORG) is None


def test_conversations_list_refetches_a_cache_of_another_version(
    run_cli: Callable[..., Result], fake_slack: FakeWebClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_conversations_to_cache(ORG, [conversation()])
    bump(monkeypatch, "slackcli.commands.conversations.CONVERSATIONS_CACHE_VERSION")
    fake_slack.on("conversations.list", {"channels": [{"id": "C0987654321", "name": "random", "is_channel": True}]})

    result = run_cli("conversations", "list")

    assert result.exit_code == 0, result.stderr
    assert len(fake_slack.calls_to("conversations.list")) == 1
    cached = load_conversations_from_cache(ORG)
    assert cached is not None
    assert [c.id for c in cached] == ["C0987654321"]


def test_channel_lookup_refetches_a_cache_of_another_version(
    run_cli: Callable[..., Result], fake_slack: FakeWebClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Undo the cli_config_path stub, so the lookup reads the real cache
    monkeypatch.setattr(SlackCli, "get_conversations_from_cache", load_conversations_for_lookup)
    save_conversations_to_cache(ORG, [conversation()])
    bump(monkeypatch, "slackcli.commands.conversations.CONVERSATIONS_CACHE_VERSION")
    fake_slack.on("conversations.list", {"channels": [{"id": "C0123456789", "name": "general", "is_channel": True}]})
    fake_slack.handle(
        "conversations.history",
        lambda **_: {"messages": [{"ts": "1700000000.000100", "text": "hi"}], "has_more": False},
    )

    result = run_cli("messages", "list", "C0123456789", "--json")

    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["channel_name"] == "general"
    assert [m["ts"] for m in data["messages"]] == ["1700000000.000100"]
    # Rich wraps stderr at the terminal width, so compare with the whitespace collapsed
    assert "another slackcli version, fetching it again" in " ".join(result.stderr.split())
    assert len(fake_slack.calls_to("conversations.list")) == 1
    assert load_conversations_from_cache(ORG) is not None


def test_channel_lookup_without_a_cache_still_asks_for_conversations_list(
    run_cli: Callable[..., Result], fake_slack: FakeWebClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SlackCli, "get_conversations_from_cache", load_conversations_for_lookup)

    result = run_cli("messages", "list", "C0123456789", "--json")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Conversations cache not found" in result.stderr
    assert fake_slack.calls == []


# --- users cache -----------------------------------------------------------------------------------------


def test_cached_user_of_the_current_version_is_fresh() -> None:
    save_user_to_cache(ORG, UserInfo.from_api(user_data("U0123456789", "alice")))

    user = load_user_from_cache(ORG, "U0123456789")

    assert user is not None
    assert not user.is_expired()
    assert [u.id for u in load_all_users_from_cache(ORG)] == ["U0123456789"]


def test_cached_user_of_another_version_is_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    save_user_to_cache(ORG, UserInfo.from_api(user_data("U0123456789", "alice")))
    bump(monkeypatch, "slackcli.users.USER_CACHE_VERSION")

    user = load_user_from_cache(ORG, "U0123456789")

    assert user is not None
    assert user.is_expired()
    # The paths without a time-based expiry leave it out
    assert load_all_users_from_cache(ORG) == []


def test_cached_user_without_a_version_counts_as_version_1(
    isolated_cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_user_to_cache(ORG, UserInfo.from_api(user_data("U0123456789", "alice")))
    strip_version(isolated_cache_dir / ORG / "users" / "U0123456789.json", "_meta")

    monkeypatch.setattr("slackcli.users.USER_CACHE_VERSION", 1)
    user = load_user_from_cache(ORG, "U0123456789")
    assert user is not None
    assert not user.is_expired()

    monkeypatch.setattr("slackcli.users.USER_CACHE_VERSION", 2)
    user = load_user_from_cache(ORG, "U0123456789")
    assert user is not None
    assert user.is_expired()


def test_get_user_refetches_a_user_cached_by_another_version(
    fake_slack: FakeWebClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_user_to_cache(ORG, UserInfo.from_api(user_data("U0123456789", "alice")))
    bump(monkeypatch, "slackcli.users.USER_CACHE_VERSION")
    fake_slack.on("users.info", {"user": user_data("U0123456789", "alice.renamed")})

    user = get_user(SlackCli(org_name=ORG, token="xoxp-test-token"), "U0123456789")

    assert user is not None
    assert user.name == "alice.renamed"
    rewritten = load_user_from_cache(ORG, "U0123456789")
    assert rewritten is not None
    assert not rewritten.is_expired()


def test_get_user_falls_back_to_a_user_cached_by_another_version_on_a_permanent_answer(
    fake_slack: FakeWebClient, monkeypatch: pytest.MonkeyPatch, make_slack_api_error: Callable[..., SlackApiError]
) -> None:
    save_user_to_cache(ORG, UserInfo.from_api(user_data("U0123456789", "alice")))
    bump(monkeypatch, "slackcli.users.USER_CACHE_VERSION")
    fake_slack.on("users.info", make_slack_api_error("user_not_found", method="users.info"))

    user = get_user(SlackCli(org_name=ORG, token="xoxp-test-token"), "U0123456789")

    assert user is not None
    assert user.name == "alice"


# --- users-list completeness marker ----------------------------------------------------------------------


def test_marker_of_the_current_version_marks_the_list_complete() -> None:
    mark_users_cache_complete(ORG, 1)

    assert is_users_cache_complete(ORG)


def test_marker_of_another_version_does_not_mark_the_list_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    mark_users_cache_complete(ORG, 1)
    bump(monkeypatch, "slackcli.users.USER_CACHE_VERSION")

    assert not is_users_cache_complete(ORG)


def test_marker_without_a_version_counts_as_version_1(
    isolated_cache_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mark_users_cache_complete(ORG, 1)
    strip_version(isolated_cache_dir / ORG / "users-list-complete.json")

    monkeypatch.setattr("slackcli.users.USER_CACHE_VERSION", 1)
    assert is_users_cache_complete(ORG)
    monkeypatch.setattr("slackcli.users.USER_CACHE_VERSION", 2)
    assert not is_users_cache_complete(ORG)


def test_unreadable_marker_does_not_mark_the_list_complete(isolated_cache_dir: Path) -> None:
    marker = isolated_cache_dir / ORG / "users-list-complete.json"
    marker.parent.mkdir(parents=True)
    marker.write_text("{not json")

    assert not is_users_cache_complete(ORG)


def test_users_list_walks_users_list_again_after_a_version_change(
    run_cli: Callable[..., Result], fake_slack: FakeWebClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_user_to_cache(ORG, UserInfo.from_api(user_data("U0123456789", "alice")))
    mark_users_cache_complete(ORG, 1)
    bump(monkeypatch, "slackcli.users.USER_CACHE_VERSION")
    fake_slack.on("users.list", {"members": [user_data("U0123456789", "alice"), user_data("U0987654321", "bob")]})

    result = run_cli("users", "list", "--json")

    assert result.exit_code == 0, result.stderr
    assert len(fake_slack.calls_to("users.list")) == 1
    assert is_users_cache_complete(ORG)
    assert sorted(u.id for u in load_all_users_from_cache(ORG)) == ["U0123456789", "U0987654321"]
