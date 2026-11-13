"""Tests for the conversations list type and membership filters."""

from __future__ import annotations

from typing import Any

from slackcli.commands.conversations import filter_conversations
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
