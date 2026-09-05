"""Tests for message composition rules."""

from __future__ import annotations

import pytest

from slackcli.compose import (
    PLAIN_TEXT_LIMIT,
    ComposeError,
    check_plain_text_length,
    preview_removal_update,
)


class TestCheckPlainTextLength:
    """Tests for check_plain_text_length()."""

    def test_empty_text_passes(self) -> None:
        check_plain_text_length("")

    def test_text_at_the_limit_passes(self) -> None:
        check_plain_text_length("y" * PLAIN_TEXT_LIMIT)

    def test_text_over_the_limit_raises(self) -> None:
        with pytest.raises(ComposeError):
            check_plain_text_length("y" * (PLAIN_TEXT_LIMIT + 1))

    def test_error_names_the_length_and_the_way_out(self) -> None:
        """The message is written for an agent that has to decide what to do next."""
        with pytest.raises(ComposeError) as excinfo:
            check_plain_text_length("y" * 4500)

        message = str(excinfo.value)
        assert "Message is 4500 characters." in message
        assert "over 4000 characters" in message
        assert "Split it into several shorter messages" in message

    def test_compose_error_is_a_value_error(self) -> None:
        assert issubclass(ComposeError, ValueError)


class TestPreviewRemovalUpdate:
    """Tests for preview_removal_update()."""

    def test_message_with_blocks_is_reposted_as_blocks_and_fallback_text(self) -> None:
        """Blocks carry the content, and the stored text stays as Slack's fallback."""
        blocks = [{"type": "rich_text", "elements": []}]
        message = {"text": "fallback", "blocks": blocks}

        assert preview_removal_update(message) == {
            "blocks": blocks,
            "text": "fallback",
            "clear_attachments": True,
        }

    def test_fallback_text_at_the_limit_is_kept(self) -> None:
        blocks = [{"type": "rich_text", "elements": []}]
        message = {"text": "y" * PLAIN_TEXT_LIMIT, "blocks": blocks}

        assert preview_removal_update(message)["text"] == "y" * PLAIN_TEXT_LIMIT

    def test_message_without_blocks_is_reposted_as_text(self) -> None:
        message = {"text": "Look: https://example.com"}

        assert preview_removal_update(message) == {"text": "Look: https://example.com", "clear_attachments": True}

    def test_fallback_text_is_dropped_only_when_over_the_limit(self) -> None:
        """chat.update rejects text over 4000 characters even when blocks carry it."""
        blocks = [{"type": "rich_text", "elements": []}]
        message = {"text": "y" * (PLAIN_TEXT_LIMIT + 1), "blocks": blocks}

        update = preview_removal_update(message)
        assert update == {"blocks": blocks, "clear_attachments": True}
        assert "text" not in update

    def test_long_text_without_blocks_raises(self) -> None:
        with pytest.raises(ComposeError):
            preview_removal_update({"text": "y" * (PLAIN_TEXT_LIMIT + 1)})

    def test_text_at_the_limit_without_blocks_passes(self) -> None:
        message = {"text": "y" * PLAIN_TEXT_LIMIT}

        assert preview_removal_update(message)["text"] == "y" * PLAIN_TEXT_LIMIT

    def test_message_with_only_attachments_raises(self) -> None:
        """Re-posting nothing would empty the message, so say so instead."""
        message = {"text": "", "attachments": [{"id": 1, "title": "Preview"}]}

        with pytest.raises(ComposeError) as excinfo:
            preview_removal_update(message)

        assert "no text or blocks of its own" in str(excinfo.value)

    def test_empty_blocks_list_falls_back_to_text(self) -> None:
        message = {"text": "Plain body", "blocks": []}

        assert preview_removal_update(message) == {"text": "Plain body", "clear_attachments": True}
