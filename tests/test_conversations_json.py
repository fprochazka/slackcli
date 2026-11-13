"""conversations list --json keeps stdout pure JSON, on the cached path and on the refresh path."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from click.testing import Result

from slackcli.commands.conversations import save_conversations_to_cache
from slackcli.models import Conversation
from slackcli.users import UserInfo, save_user_to_cache

from .conftest import FakeWebClient

RunCli = Callable[..., Result]

ORG = "testorg"


def conversation(conversation_id: str, name: str, **flags: Any) -> Conversation:
    fields: dict[str, Any] = {
        "id": conversation_id,
        "name": name,
        "is_private": False,
        "is_channel": False,
        "is_group": False,
        "is_im": False,
        "is_mpim": False,
        "is_member": True,
        "topic": "",
        "purpose": "",
        "num_members": 0,
        "created": 0,
    }
    fields.update(flags)
    return Conversation(**fields)


def user_data(user_id: str, name: str) -> dict[str, Any]:
    return {"id": user_id, "name": name, "real_name": name.title(), "profile": {"display_name": name}}


PUBLIC = conversation("C0000000001", "general", is_channel=True)
PRIVATE = conversation("C0000000002", "secret", is_channel=True, is_private=True)
ARCHIVED = conversation("C0000000003", "old", is_channel=True, is_archived=True)
DM = conversation("D0000000004", "U0123456789", is_im=True, user_id="U0123456789")
GROUP_DM = conversation(
    "C0000000005",
    "mpdm-bob--alice-1",
    is_channel=True,
    is_private=True,
    is_mpim=True,
    member_ids=["U0987654321", "U0123456789"],
)


@pytest.fixture
def cached_directory() -> None:
    save_conversations_to_cache(ORG, [PUBLIC, PRIVATE, ARCHIVED, DM, GROUP_DM])
    save_user_to_cache(ORG, UserInfo.from_api(user_data("U0123456789", "alice")))
    save_user_to_cache(ORG, UserInfo.from_api(user_data("U0987654321", "bob")))


@pytest.mark.usefixtures("cached_directory")
def test_json_from_the_cache_is_the_only_thing_on_stdout(run_cli: RunCli) -> None:
    result = run_cli("conversations", "list", "--json")

    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["count"] == 5
    rows = {row["id"]: row for row in data["conversations"]}
    assert rows[PUBLIC.id]["type"] == "public_channel"
    assert rows[PUBLIC.id]["display_name"] == "general"
    assert rows[PRIVATE.id]["type"] == "private_channel"
    assert rows[ARCHIVED.id]["is_archived"] is True
    assert rows[DM.id]["type"] == "im"
    assert rows[DM.id]["display_name"] == "alice"
    assert rows[GROUP_DM.id]["type"] == "mpim"
    assert rows[GROUP_DM.id]["display_name"] == "alice, bob"
    assert rows[GROUP_DM.id]["member_ids"] == ["U0987654321", "U0123456789"]
    assert "Using cached conversations" in result.stderr


@pytest.mark.usefixtures("cached_directory")
def test_json_applies_the_filters(run_cli: RunCli) -> None:
    result = run_cli("conversations", "list", "--json", "--public", "--no-archived")

    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["count"] == 1
    assert [row["id"] for row in data["conversations"]] == [PUBLIC.id]


@pytest.mark.usefixtures("cached_directory")
def test_json_shape_is_kept_apart_from_the_cache_shape(run_cli: RunCli, isolated_cache_dir: Path) -> None:
    run_cli("conversations", "list", "--json")

    cached = json.loads((isolated_cache_dir / ORG / "conversations.json").read_text())["data"]["conversations"]
    assert all("type" not in row and "display_name" not in row for row in cached)


def test_json_after_a_refresh_is_the_only_thing_on_stdout(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    fake_slack.on(
        "conversations.list",
        {
            "channels": [
                {"id": PUBLIC.id, "name": "general", "is_channel": True, "is_member": True},
                {"id": DM.id, "is_im": True, "user": "U0123456789"},
                {"id": GROUP_DM.id, "name": GROUP_DM.name, "is_channel": True, "is_private": True, "is_mpim": True},
            ]
        },
    )
    fake_slack.on("conversations.members", {"members": ["U0987654321", "U0123456789"]})
    fake_slack.handle(
        "users.info",
        lambda user, **_: {"user": user_data(user, {"U0123456789": "alice", "U0987654321": "bob"}[user])},
    )

    result = run_cli("conversations", "list", "--json", "--refresh")

    assert result.exit_code == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["count"] == 3
    rows = {row["id"]: row for row in data["conversations"]}
    assert rows[DM.id]["display_name"] == "alice"
    assert rows[GROUP_DM.id]["display_name"] == "alice, bob"
    for progress in ("Fetching conversations from Slack API", "Fetching members for 1 group DMs", "Cache updated"):
        assert progress in result.stderr


@pytest.mark.usefixtures("cached_directory")
def test_text_output_keeps_its_progress_lines_on_stdout(run_cli: RunCli) -> None:
    result = run_cli("conversations", "list")

    assert result.exit_code == 0, result.stderr
    assert "Using cached conversations" in result.stdout
    assert "Total: 5 conversations" in result.stdout


# --- the refresh notice names its reason -------------------------------------------------------------------


def refresh_with_one_channel(fake_slack: FakeWebClient) -> None:
    fake_slack.on("conversations.list", {"channels": [{"id": PUBLIC.id, "name": "general", "is_channel": True}]})


def test_refresh_notice_names_a_cache_from_another_version(
    run_cli: RunCli, fake_slack: FakeWebClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_conversations_to_cache(ORG, [PUBLIC])
    monkeypatch.setattr("slackcli.commands.conversations.CONVERSATIONS_CACHE_VERSION", 99)
    refresh_with_one_channel(fake_slack)

    result = run_cli("conversations", "list", "--json")

    assert result.exit_code == 0, result.stderr
    assert "Cache was written by another slackcli version, refreshing" in result.stderr
    assert "older than 6 hours" not in result.stderr


def test_refresh_notice_names_an_old_cache(
    run_cli: RunCli, fake_slack: FakeWebClient, isolated_cache_dir: Path
) -> None:
    save_conversations_to_cache(ORG, [PUBLIC])
    cache_path = isolated_cache_dir / ORG / "conversations.json"
    cache = json.loads(cache_path.read_text())
    cache["metadata"]["updated_at"] = (datetime.now() - timedelta(hours=7)).isoformat()
    cache_path.write_text(json.dumps(cache))
    refresh_with_one_channel(fake_slack)

    result = run_cli("conversations", "list", "--json")

    assert result.exit_code == 0, result.stderr
    assert "Cache is older than 6 hours, refreshing" in result.stderr


def test_no_refresh_notice_when_there_is_no_cache_yet(run_cli: RunCli, fake_slack: FakeWebClient) -> None:
    refresh_with_one_channel(fake_slack)

    result = run_cli("conversations", "list", "--json")

    assert result.exit_code == 0, result.stderr
    assert "refreshing" not in result.stderr
    assert "Fetching conversations from Slack API" in result.stderr
