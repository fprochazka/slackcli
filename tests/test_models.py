"""Tests for data models."""

from __future__ import annotations

from typing import Any

from slackcli.blocks import get_message_body_text, get_message_text
from slackcli.models import Message, format_file_size, resolve_slack_mentions


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
