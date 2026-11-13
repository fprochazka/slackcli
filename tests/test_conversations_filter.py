"""Tests for the conversations list type, membership and archive filters."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from click.testing import Result

from slackcli.commands.conversations import filter_conversations, save_conversations_to_cache
from slackcli.models import Conversation


def conversation(conversation_id: str, **flags: Any) -> Conversation:
    fields: dict[str, Any] = {
        "id": conversation_id,
        "name": conversation_id.lower(),
        "is_private": False,
        "is_channel": False,
        "is_group": False,
        "is_im": False,
        "is_mpim": False,
        "is_member": True,
        "topic": "",
        "purpose": "",
        "num_members": 3,
        "created": 0,
    }
    fields.update(flags)
    return Conversation(**fields)


PUBLIC = conversation("C0000000001", is_channel=True)
PRIVATE = conversation("C0000000002", is_channel=True, is_private=True)
# Slack returns a group DM flagged as a private channel as well
GROUP_DM = conversation("C0000000003", is_channel=True, is_private=True, is_mpim=True)
DM = conversation("D0000000004", is_im=True, user_id="U0123456789")

ALL = [PUBLIC, PRIVATE, GROUP_DM, DM]


def ids(conversations: list[Conversation]) -> list[str]:
    return [c.id for c in conversations]


def test_private_lists_private_channels_without_group_dms() -> None:
    assert ids(filter_conversations(ALL, private=True)) == [PRIVATE.id]


def test_dms_still_lists_group_dms() -> None:
    assert ids(filter_conversations(ALL, dms=True)) == [GROUP_DM.id, DM.id]


def test_private_and_dms_together_list_each_once() -> None:
    assert ids(filter_conversations(ALL, private=True, dms=True)) == [PRIVATE.id, GROUP_DM.id, DM.id]


def test_public_lists_only_public_channels() -> None:
    assert ids(filter_conversations(ALL, public=True)) == [PUBLIC.id]


# --- archived conversations --------------------------------------------------------------------------------

ARCHIVED_PUBLIC = conversation("C0000000005", is_channel=True, is_archived=True)
ARCHIVED_PRIVATE = conversation("C0000000006", is_channel=True, is_private=True, is_archived=True)
WITH_ARCHIVED = [PUBLIC, PRIVATE, ARCHIVED_PUBLIC, ARCHIVED_PRIVATE, DM]


def test_archived_conversations_are_shown_by_default() -> None:
    assert ids(filter_conversations(WITH_ARCHIVED)) == ids(WITH_ARCHIVED)


def test_archived_shows_only_archived_conversations() -> None:
    assert ids(filter_conversations(WITH_ARCHIVED, archived=True)) == [ARCHIVED_PUBLIC.id, ARCHIVED_PRIVATE.id]


def test_no_archived_hides_archived_conversations() -> None:
    assert ids(filter_conversations(WITH_ARCHIVED, archived=False)) == [PUBLIC.id, PRIVATE.id, DM.id]


def test_archive_filter_combines_with_type_and_membership_filters() -> None:
    assert ids(filter_conversations(WITH_ARCHIVED, public=True, archived=True)) == [ARCHIVED_PUBLIC.id]
    assert ids(filter_conversations(WITH_ARCHIVED, private=True, archived=False)) == [PRIVATE.id]
    assert ids(filter_conversations(WITH_ARCHIVED, non_member=True, archived=True)) == []


def test_is_archived_survives_the_api_and_the_cache() -> None:
    from_api = Conversation.from_api({"id": "C0000000005", "name": "old", "is_channel": True, "is_archived": True})
    assert from_api.is_archived
    assert from_api.to_dict()["is_archived"] is True
    assert Conversation.from_dict(from_api.to_dict()).is_archived
    assert not Conversation.from_api({"id": "C0000000001", "name": "live", "is_channel": True}).is_archived


def test_conversations_list_marks_archived_rows_and_filters_them(run_cli: Callable[..., Result]) -> None:
    save_conversations_to_cache("testorg", [PUBLIC, ARCHIVED_PUBLIC])

    default = run_cli("conversations", "list")
    only_archived = run_cli("conversations", "list", "--archived")
    without_archived = run_cli("conversations", "list", "--no-archived")

    assert default.exit_code == 0, default.stderr
    assert f"{PUBLIC.id}: {PUBLIC.name} (Public Channel)" in default.stdout
    assert f"{ARCHIVED_PUBLIC.id}: {ARCHIVED_PUBLIC.name} (Public Channel, archived)" in default.stdout
    assert ARCHIVED_PUBLIC.id in only_archived.stdout
    assert PUBLIC.id not in only_archived.stdout
    assert PUBLIC.id in without_archived.stdout
    assert ARCHIVED_PUBLIC.id not in without_archived.stdout
