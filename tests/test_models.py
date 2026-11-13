"""Tests for data models."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from click.testing import Result

from slackcli.blocks import get_message_body_text, get_message_text
from slackcli.models import Message, MessagesOutput, format_file_size, resolve_slack_mentions
from slackcli.output import output_messages_text

from .conftest import FakeWebClient


class TestFormatFileSize:
    """Tests for format_file_size()."""

    def test_bytes(self) -> None:
        """Test formatting sizes in bytes."""
        assert format_file_size(0) == "0 B"
        assert format_file_size(1) == "1 B"
        assert format_file_size(512) == "512 B"
        assert format_file_size(1023) == "1023 B"

    def test_kilobytes(self) -> None:
        """Test formatting sizes in KB."""
        assert format_file_size(1024) == "1.0 KB"
        assert format_file_size(1536) == "1.5 KB"
        assert format_file_size(10 * 1024) == "10.0 KB"
        assert format_file_size(100 * 1024) == "100.0 KB"
        assert format_file_size(1024 * 1024 - 1) == "1024.0 KB"

    def test_megabytes(self) -> None:
        """Test formatting sizes in MB."""
        assert format_file_size(1024 * 1024) == "1.0 MB"
        assert format_file_size(int(1.5 * 1024 * 1024)) == "1.5 MB"
        assert format_file_size(10 * 1024 * 1024) == "10.0 MB"
        assert format_file_size(100 * 1024 * 1024) == "100.0 MB"
        assert format_file_size(1024 * 1024 * 1024 - 1) == "1024.0 MB"

    def test_gigabytes(self) -> None:
        """Test formatting sizes in GB."""
        assert format_file_size(1024 * 1024 * 1024) == "1.0 GB"
        assert format_file_size(int(1.5 * 1024 * 1024 * 1024)) == "1.5 GB"
        assert format_file_size(10 * 1024 * 1024 * 1024) == "10.0 GB"

    def test_edge_cases(self) -> None:
        """Test edge cases at unit boundaries."""
        # Just below 1 KB
        assert format_file_size(1023) == "1023 B"
        # Exactly 1 KB
        assert format_file_size(1024) == "1.0 KB"

        # Just below 1 MB
        assert format_file_size(1024 * 1024 - 1) == "1024.0 KB"
        # Exactly 1 MB
        assert format_file_size(1024 * 1024) == "1.0 MB"

        # Just below 1 GB
        assert format_file_size(1024 * 1024 * 1024 - 1) == "1024.0 MB"
        # Exactly 1 GB
        assert format_file_size(1024 * 1024 * 1024) == "1.0 GB"

    def test_decimal_precision(self) -> None:
        """Test that decimal precision is one digit."""
        # 1.23 KB should be shown as 1.2 KB
        assert format_file_size(1260) == "1.2 KB"
        # 1.99 KB
        assert format_file_size(2037) == "2.0 KB"

    def test_large_values(self) -> None:
        """Test very large file sizes."""
        # 100 GB
        assert format_file_size(100 * 1024 * 1024 * 1024) == "100.0 GB"
        # 1 TB (represented as 1024 GB)
        assert format_file_size(1024 * 1024 * 1024 * 1024) == "1024.0 GB"


class TestMessageAttachments:
    """Tests for Message.attachments and Message.display_text."""

    USERS: dict[str, str] = {"U001": "alice"}
    CHANNELS: dict[str, str] = {"C001": "general"}

    APP_UNFURL: dict[str, Any] = {
        "text": "Look at this <@U001> <https://linear.app/acme/issue/ENG-1|ENG-1>",
        "blocks": [
            {
                "type": "rich_text",
                "elements": [
                    {
                        "type": "rich_text_section",
                        "elements": [
                            {"type": "text", "text": "Look at this "},
                            {"type": "user", "user_id": "U001"},
                            {"type": "text", "text": " "},
                            {"type": "link", "url": "https://linear.app/acme/issue/ENG-1", "text": "ENG-1"},
                        ],
                    }
                ],
            }
        ],
        "ts": "1700000000.000100",
        "user": "U001",
        "attachments": [
            {
                "id": 1,
                "app_id": "A0F7YS32P",
                "is_app_unfurl": True,
                "service_name": "Linear",
                "title": "ENG-1 Fix the thing",
                "title_link": "https://linear.app/acme/issue/ENG-1",
                "text": "Reported by <@U001>",
                "from_url": "https://linear.app/acme/issue/ENG-1",
            }
        ],
    }

    def _message(self) -> Message:
        return Message.from_api(
            self.APP_UNFURL,
            self.USERS,
            self.CHANNELS,
            get_message_body_text,
            resolve_slack_mentions,
        )

    def test_text_holds_only_the_body(self) -> None:
        """The author's text is kept apart from the unfurled preview."""
        msg = self._message()
        assert msg.text == "Look at this @alice ENG-1 (https://linear.app/acme/issue/ENG-1)"
        assert "Fix the thing" not in msg.text

    def test_attachment_fields(self) -> None:
        """Attachment metadata is parsed from the API payload."""
        msg = self._message()
        assert len(msg.attachments) == 1
        attachment = msg.attachments[0]
        assert attachment.id == 1
        assert attachment.from_url == "https://linear.app/acme/issue/ENG-1"
        assert attachment.is_app_unfurl is True
        assert attachment.app_id == "A0F7YS32P"
        assert attachment.service_name == "Linear"
        assert attachment.title == "ENG-1 Fix the thing"
        assert attachment.title_link == "https://linear.app/acme/issue/ENG-1"
        assert "Reported by @alice" in attachment.text

    def test_display_text_matches_the_merged_rendering(self) -> None:
        """Text output still shows body and attachments merged as before."""
        msg = self._message()
        merged = resolve_slack_mentions(
            get_message_text(self.APP_UNFURL, self.USERS, self.CHANNELS),
            self.USERS,
            self.CHANNELS,
        )
        assert msg.display_text == merged
        assert msg.display_text.startswith(msg.text)

    def test_to_dict_includes_attachments(self) -> None:
        """JSON output carries attachments next to files."""
        result = self._message().to_dict()
        keys = list(result)
        assert keys.index("attachments") == keys.index("files") + 1
        assert result["attachments"][0]["from_url"] == "https://linear.app/acme/issue/ENG-1"
        assert "Fix the thing" not in result["text"]

    def test_display_text_without_attachments(self) -> None:
        """A message with no attachments displays its body unchanged."""
        data = {"ts": "1700000000.000200", "user": "U001", "text": "Just text"}
        msg = Message.from_api(data, self.USERS, self.CHANNELS, get_message_body_text, resolve_slack_mentions)
        assert msg.attachments == []
        assert msg.display_text == "Just text"

    def test_body_kept_when_message_has_no_blocks(self) -> None:
        """A link plus its unfurl keeps the author's text next to the preview."""
        data = {
            "ts": "1700000000.000300",
            "user": "U001",
            "text": "Read this: https://example.com",
            "attachments": [{"id": 1, "title": "Example", "from_url": "https://example.com"}],
        }
        msg = Message.from_api(data, self.USERS, self.CHANNELS, get_message_body_text, resolve_slack_mentions)
        assert msg.text == "Read this: https://example.com"
        assert msg.display_text == "Read this: https://example.com\nExample\nhttps://example.com"


class TestMessageBotIdentityAndThreadSummary:
    """Tests for the bot identity and thread summary fields of Message."""

    USERS: dict[str, str] = {"U0123456789": "alice", "U0987654321": "deploy-bot"}
    CHANNELS: dict[str, str] = {}

    HUMAN_THREAD_ROOT: dict[str, Any] = {
        "ts": "1700000000.000100",
        "user": "U0123456789",
        "text": "Shall we ship it?",
        "thread_ts": "1700000000.000100",
        "reply_count": 4,
        "reply_users_count": 2,
        "reply_users": ["U0123456789", "B0123456789"],
        "latest_reply": "1700003600.000200",
    }

    WEBHOOK_POST: dict[str, Any] = {
        "ts": "1700000000.000300",
        "type": "message",
        "subtype": "bot_message",
        "bot_id": "B0123456789",
        "username": "Build Pipeline",
        "text": "Build #42 passed",
    }

    BOT_USER_POST: dict[str, Any] = {
        "ts": "1700000000.000400",
        "type": "message",
        "user": "U0987654321",
        "bot_id": "B0987654321",
        "bot_profile": {"id": "B0987654321", "name": "Deploy Bot", "app_id": "A0123456789"},
        "text": "Deployed",
    }

    def _message(self, data: dict[str, Any]) -> Message:
        return Message.from_api(data, self.USERS, self.CHANNELS, get_message_body_text, resolve_slack_mentions)

    def test_human_thread_root_carries_the_thread_summary(self) -> None:
        result = self._message(self.HUMAN_THREAD_ROOT).to_dict()
        assert result["user_name"] == "alice"
        assert result["reply_count"] == 4
        assert result["reply_users_count"] == 2
        # Raw IDs, bot IDs included, never resolved to names
        assert result["reply_user_ids"] == ["U0123456789", "B0123456789"]
        assert result["latest_reply"] == "1700003600.000200"
        assert result["bot_id"] is None
        assert result["bot_name"] is None
        assert result["username"] is None
        assert result["subtype"] is None

    def test_webhook_post_carries_bot_identity_without_a_user_name(self) -> None:
        result = self._message(self.WEBHOOK_POST).to_dict()
        assert result["user_id"] is None
        # user_name stays a Slack handle, and a webhook's free-text username is not one
        assert result["user_name"] is None
        assert result["username"] == "Build Pipeline"
        assert result["bot_id"] == "B0123456789"
        assert result["subtype"] == "bot_message"
        assert result["bot_name"] is None

    def test_bot_user_post_carries_the_bot_profile_name(self) -> None:
        result = self._message(self.BOT_USER_POST).to_dict()
        assert result["user_id"] == "U0987654321"
        # The bot user's handle and its profile name are separate fields
        assert result["user_name"] == "deploy-bot"
        assert result["bot_id"] == "B0987654321"
        assert result["bot_name"] == "Deploy Bot"
        assert result["username"] is None

    def test_fields_are_always_present_with_empty_defaults(self) -> None:
        result = self._message({"ts": "1700000000.000500", "user": "U0123456789", "text": "hi"}).to_dict()
        assert result["bot_id"] is None
        assert result["bot_name"] is None
        assert result["username"] is None
        assert result["subtype"] is None
        assert result["latest_reply"] is None
        assert result["reply_users_count"] == 0
        assert result["reply_user_ids"] == []

    def test_fields_are_present_on_inline_replies(self) -> None:
        data = dict(self.HUMAN_THREAD_ROOT, replies=[dict(self.WEBHOOK_POST, thread_ts=self.HUMAN_THREAD_ROOT["ts"])])
        reply = self._message(data).to_dict()["replies"][0]
        assert reply["username"] == "Build Pipeline"
        assert reply["reply_user_ids"] == []
        assert reply["latest_reply"] is None

    def _render(self, capsys: pytest.CaptureFixture[str], *messages: dict[str, Any]) -> str:
        output = MessagesOutput(
            channel_id="C0123456789", channel_name="general", messages=[self._message(m) for m in messages]
        )
        output_messages_text(output)
        return capsys.readouterr().out

    def test_text_shows_a_webhook_username_without_the_at_sign(self, capsys: pytest.CaptureFixture[str]) -> None:
        header = self._render(capsys, self.WEBHOOK_POST).splitlines()[0]
        assert header.endswith("  Build Pipeline")
        assert "@" not in header

    def test_text_falls_back_to_the_bot_profile_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = {k: v for k, v in self.BOT_USER_POST.items() if k != "user"}
        assert self._render(capsys, data).splitlines()[0].endswith("  Deploy Bot")

    def test_text_keeps_the_handle_of_a_bot_user(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert self._render(capsys, self.BOT_USER_POST).splitlines()[0].endswith("  @deploy-bot")

    def test_text_shows_unknown_when_nothing_names_the_author(self, capsys: pytest.CaptureFixture[str]) -> None:
        data = {"ts": "1700000000.000600", "subtype": "bot_message", "text": "anonymous"}
        assert self._render(capsys, data).splitlines()[0].endswith("  @(unknown)")

    def test_text_meta_line_shows_the_last_reply(self, capsys: pytest.CaptureFixture[str]) -> None:
        out = self._render(capsys, self.HUMAN_THREAD_ROOT)
        assert "[4 replies, last reply 2023-11-14 23:13:20, thread_ts=1700000000.000100]" in out


def test_messages_list_reports_the_summary_without_resolving_reply_user_ids(
    run_cli: Callable[..., Result], fake_slack: FakeWebClient
) -> None:
    root = dict(TestMessageBotIdentityAndThreadSummary.HUMAN_THREAD_ROOT)
    del root["user"]
    root.update(subtype="bot_message", bot_id="B0123456789", username="Build Pipeline")
    fake_slack.on("conversations.history", {"messages": [root], "has_more": False})

    result = run_cli("messages", "list", "C0123456789", "--json")

    assert result.exit_code == 0, result.stderr
    message = json.loads(result.stdout)["messages"][0]
    assert message["reply_user_ids"] == ["U0123456789", "B0123456789"]
    assert message["username"] == "Build Pipeline"
    assert message["user_name"] is None
    # The FakeWebClient fails on any unscripted call, so no users.info call was made for the reply_users IDs
    assert fake_slack.calls_to("users.info") == []
