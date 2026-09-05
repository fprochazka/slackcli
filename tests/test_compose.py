"""Tests for message composition rules."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from slackcli.compose import (
    MAX_BLOCKS,
    PLAIN_TEXT_LIMIT,
    RICH_TEXT_LIMIT,
    SECTION_TEXT_LIMIT,
    ComposeError,
    check_blocks,
    check_plain_text_length,
    compose_message,
    load_blocks,
    preview_removal_update,
)
from slackcli.signature import Signature


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


def rich_text_block(text: str) -> dict:
    """Build a rich_text block holding one line of text."""
    return {
        "type": "rich_text",
        "elements": [{"type": "rich_text_section", "elements": [{"type": "text", "text": text}]}],
    }


class TestLoadBlocks:
    """Tests for load_blocks()."""

    def test_bare_array(self, tmp_path: Path) -> None:
        path = tmp_path / "blocks.json"
        path.write_text(json.dumps([{"type": "divider"}]))

        assert load_blocks(str(path)) == [{"type": "divider"}]

    def test_block_kit_builder_export(self, tmp_path: Path) -> None:
        """The Block Kit Builder exports an object with a blocks key."""
        path = tmp_path / "blocks.json"
        path.write_text(json.dumps({"blocks": [{"type": "divider"}]}))

        assert load_blocks(str(path)) == [{"type": "divider"}]

    def test_object_without_blocks_key_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "blocks.json"
        path.write_text(json.dumps({"type": "divider"}))

        with pytest.raises(ComposeError) as excinfo:
            load_blocks(str(path))

        assert "'blocks' key" in str(excinfo.value)

    def test_scalar_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "blocks.json"
        path.write_text('"just a string"')

        with pytest.raises(ComposeError) as excinfo:
            load_blocks(str(path))

        assert "not an array of blocks" in str(excinfo.value)

    def test_invalid_json_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "blocks.json"
        path.write_text("{not json")

        with pytest.raises(ComposeError) as excinfo:
            load_blocks(str(path))

        assert "not valid JSON" in str(excinfo.value)

    def test_blank_stdin_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Nothing piped in is a clearer complaint than a JSON parse error."""
        monkeypatch.setattr("sys.stdin", io.StringIO("   \n"))

        with pytest.raises(ComposeError) as excinfo:
            load_blocks("-")

        assert str(excinfo.value) == "No blocks were read from stdin."

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ComposeError) as excinfo:
            load_blocks(str(tmp_path / "nope.json"))

        assert "Cannot read blocks" in str(excinfo.value)


class TestCheckBlocks:
    """Tests for check_blocks()."""

    def test_valid_blocks_pass(self) -> None:
        check_blocks([{"type": "divider"}, rich_text_block("hello")])

    def test_empty_list_raises(self) -> None:
        with pytest.raises(ComposeError):
            check_blocks([])

    def test_block_without_type_raises(self) -> None:
        with pytest.raises(ComposeError) as excinfo:
            check_blocks([{"text": "no type here"}])

        assert "no 'type'" in str(excinfo.value)

    def test_non_object_block_raises(self) -> None:
        with pytest.raises(ComposeError):
            check_blocks(["divider"])  # type: ignore[list-item]

    def test_block_count_at_the_limit_passes(self) -> None:
        check_blocks([{"type": "divider"}] * MAX_BLOCKS)

    def test_too_many_blocks_raises(self) -> None:
        with pytest.raises(ComposeError) as excinfo:
            check_blocks([{"type": "divider"}] * (MAX_BLOCKS + 1))

        assert f"at most {MAX_BLOCKS}" in str(excinfo.value)

    def test_section_text_at_the_limit_passes(self) -> None:
        block = {"type": "section", "text": {"type": "mrkdwn", "text": "y" * SECTION_TEXT_LIMIT}}
        check_blocks([block])

    def test_section_text_over_the_limit_raises(self) -> None:
        block = {"type": "section", "text": {"type": "mrkdwn", "text": "y" * (SECTION_TEXT_LIMIT + 1)}}

        with pytest.raises(ComposeError) as excinfo:
            check_blocks([block])

        assert str(SECTION_TEXT_LIMIT) in str(excinfo.value)

    def test_section_field_over_the_limit_raises(self) -> None:
        block = {
            "type": "section",
            "fields": [{"type": "mrkdwn", "text": "y" * (SECTION_TEXT_LIMIT + 1)}],
        }

        with pytest.raises(ComposeError):
            check_blocks([block])

    def test_context_text_over_the_limit_raises(self) -> None:
        block = {"type": "context", "elements": [{"type": "mrkdwn", "text": "y" * (SECTION_TEXT_LIMIT + 1)}]}

        with pytest.raises(ComposeError):
            check_blocks([block])

    def test_rich_text_is_counted_across_all_blocks(self) -> None:
        """The rich text limit is per message, not per block."""
        blocks = [rich_text_block("y" * 7000), rich_text_block("y" * 6000)]

        with pytest.raises(ComposeError) as excinfo:
            check_blocks(blocks)

        assert "13000 characters of rich text" in str(excinfo.value)

    def test_rich_text_at_the_limit_passes(self) -> None:
        check_blocks([rich_text_block("y" * RICH_TEXT_LIMIT)])

    def test_nested_rich_text_elements_are_counted(self) -> None:
        block = {
            "type": "rich_text",
            "elements": [
                {
                    "type": "rich_text_list",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "y" * (RICH_TEXT_LIMIT + 1)}],
                        }
                    ],
                }
            ],
        }

        with pytest.raises(ComposeError):
            check_blocks([block])


class TestComposeMessage:
    """Tests for compose_message()."""

    def test_plain_body(self) -> None:
        composed = compose_message("Hello world")

        assert composed.format == "mrkdwn"
        assert composed.blocks is None
        assert composed.text == "Hello world"

    def test_plain_body_over_the_limit_raises(self) -> None:
        with pytest.raises(ComposeError):
            compose_message("y" * (PLAIN_TEXT_LIMIT + 1))

    def test_blocks_derive_the_fallback_text(self) -> None:
        """Slack needs a text fallback for notifications and search."""
        composed = compose_message(None, blocks=[rich_text_block("Hello from blocks")])

        assert composed.format == "blocks"
        assert composed.text == "Hello from blocks"
        assert composed.blocks is not None

    def test_body_wins_over_the_derived_fallback(self) -> None:
        composed = compose_message("Custom fallback", blocks=[rich_text_block("Hello from blocks")])

        assert composed.text == "Custom fallback"
        assert composed.format == "blocks"

    def test_derived_fallback_is_shortened_to_fit(self) -> None:
        """A long rich message still gets a fallback, because chat.update caps text at 4000."""
        composed = compose_message(None, blocks=[rich_text_block("y" * (PLAIN_TEXT_LIMIT + 500))])

        assert len(composed.text) == PLAIN_TEXT_LIMIT
        assert composed.text.endswith("…")

    def test_given_fallback_over_the_limit_raises(self) -> None:
        """chat.update caps the text at 4000 even when blocks carry the content."""
        with pytest.raises(ComposeError) as excinfo:
            compose_message("y" * (PLAIN_TEXT_LIMIT + 1), blocks=[rich_text_block("hi")])

        message = str(excinfo.value)
        assert "Fallback text is 4001 characters." in message
        assert "splits plain messages" not in message

    def test_empty_body_falls_back_to_the_derived_text(self) -> None:
        """An empty body must not become an empty fallback text."""
        composed = compose_message("", blocks=[rich_text_block("Hello from blocks")])

        assert composed.text == "Hello from blocks"

    def test_blank_body_falls_back_to_the_derived_text(self) -> None:
        composed = compose_message("   ", blocks=[rich_text_block("Hello from blocks")])

        assert composed.text == "Hello from blocks"

    def test_block_limits_are_enforced(self) -> None:
        with pytest.raises(ComposeError):
            compose_message(None, blocks=[{"type": "divider"}] * (MAX_BLOCKS + 1))


class TestComposeMessageFormat:
    """Tests for how compose_message() decides between Markdown and plain mrkdwn."""

    MARKDOWN = "Look at **this**\n\n- one\n- two"
    MRKDWN = "*bold* and <https://example.com|a link>"

    def test_auto_detects_markdown(self) -> None:
        composed = compose_message(self.MARKDOWN)

        assert composed.format == "markdown"
        assert composed.blocks is not None
        assert composed.blocks[0]["type"] == "rich_text"

    def test_auto_leaves_mrkdwn_alone(self) -> None:
        """A plain Slack message must go out exactly as it was written."""
        composed = compose_message(self.MRKDWN)

        assert composed.format == "mrkdwn"
        assert composed.blocks is None
        assert composed.text == self.MRKDWN

    def test_markdown_is_forced(self) -> None:
        composed = compose_message(self.MRKDWN, format="markdown")

        assert composed.format == "markdown"
        assert composed.blocks is not None

    def test_mrkdwn_is_forced(self) -> None:
        composed = compose_message(self.MARKDOWN, format="mrkdwn")

        assert composed.format == "mrkdwn"
        assert composed.blocks is None
        assert composed.text == self.MARKDOWN

    def test_the_body_stays_the_fallback_text(self) -> None:
        composed = compose_message(self.MARKDOWN)

        assert composed.text == self.MARKDOWN

    def test_long_markdown_keeps_its_blocks_and_shortens_the_fallback(self) -> None:
        """Rich text holds far more than a plain message, so only the fallback is cut."""
        body = "**bold**\n\n" + "y" * (PLAIN_TEXT_LIMIT + 500)

        composed = compose_message(body)

        assert composed.format == "markdown"
        assert len(composed.text) == PLAIN_TEXT_LIMIT
        assert composed.text.endswith("…")

    def test_converted_output_is_checked_against_the_block_limits(self) -> None:
        body = "**bold**\n\n" + "y" * (RICH_TEXT_LIMIT + 1)

        with pytest.raises(ComposeError) as excinfo:
            compose_message(body)

        assert "rich text" in str(excinfo.value)

    def test_markdown_that_converts_to_nothing_falls_back_to_plain(self) -> None:
        composed = compose_message("```\n```", format="markdown")

        assert composed.format == "mrkdwn"
        assert composed.blocks is None

    def test_blocks_take_precedence_over_the_format(self) -> None:
        composed = compose_message(self.MARKDOWN, blocks=[rich_text_block("hi")], format="markdown")

        assert composed.format == "blocks"
        assert composed.blocks == [rich_text_block("hi")]


class TestComposeMessageSignature:
    """Tests for the agent signature footer."""

    FOOTER = Signature(agent="Claude Code", mode="marketing")
    FOOTER_BLOCK = FOOTER.block()

    def test_plain_body_becomes_a_section_plus_the_footer(self) -> None:
        """Variant B: the body reads as a normal message with grey type under it."""
        composed = compose_message("Hello there", signature=self.FOOTER)

        assert composed.format == "mrkdwn"
        assert composed.text == "Hello there"
        assert composed.blocks == [
            {"type": "section", "text": {"type": "mrkdwn", "text": "Hello there"}},
            self.FOOTER_BLOCK,
        ]

    def test_markdown_body_keeps_its_rich_text_and_gains_the_footer(self) -> None:
        composed = compose_message("Look at **this**", signature=self.FOOTER)

        assert composed.format == "markdown"
        assert composed.blocks is not None
        assert composed.blocks[0]["type"] == "rich_text"
        assert composed.blocks[-1] == self.FOOTER_BLOCK

    def test_raw_blocks_gain_the_footer(self) -> None:
        composed = compose_message(None, blocks=[rich_text_block("hi")], signature=self.FOOTER)

        assert composed.format == "blocks"
        assert composed.blocks == [rich_text_block("hi"), self.FOOTER_BLOCK]

    def test_derived_fallback_text_excludes_the_footer(self) -> None:
        """The footer is not part of what the author wrote, so it stays out of the preview."""
        composed = compose_message(None, blocks=[rich_text_block("hi")], signature=self.FOOTER)

        assert composed.text == "hi"

    def test_a_body_too_long_for_a_section_is_signed_with_a_trailing_line(self) -> None:
        body = "y" * (SECTION_TEXT_LIMIT + 1)

        composed = compose_message(body, signature=self.FOOTER)

        assert composed.blocks is None
        assert composed.format == "mrkdwn"
        assert composed.text == f"{body}\n\n_{self.FOOTER.mrkdwn()}_"

    def test_a_trailing_line_signature_counts_towards_the_plain_limit(self) -> None:
        with pytest.raises(ComposeError):
            compose_message("y" * PLAIN_TEXT_LIMIT, signature=self.FOOTER)

    def test_the_footer_counts_towards_the_block_limit(self) -> None:
        blocks = [{"type": "divider"}] * MAX_BLOCKS

        with pytest.raises(ComposeError) as excinfo:
            compose_message(None, blocks=blocks, signature=self.FOOTER)

        assert f"at most {MAX_BLOCKS}" in str(excinfo.value)

    def test_without_a_signature_nothing_changes(self) -> None:
        """A human's plain message must go out exactly as it did before signing existed."""
        composed = compose_message("Hello there")

        assert composed.blocks is None
        assert composed.text == "Hello there"

    def test_malformed_blocks_are_named_not_crashed_on(self) -> None:
        """The blocks are checked before anything tries to render them."""
        with pytest.raises(ComposeError) as excinfo:
            compose_message(None, blocks=[1, 2], signature=self.FOOTER)

        assert "Block 0 is int" in str(excinfo.value)

    def test_a_body_the_signature_pushes_over_the_limit_says_so(self) -> None:
        """The reported length is what the user typed, not the signed total."""
        footer_length = len(f"\n\n_{self.FOOTER.mrkdwn()}_")
        body = "y" * (PLAIN_TEXT_LIMIT - footer_length + 1)

        with pytest.raises(ComposeError) as excinfo:
            compose_message(body, signature=self.FOOTER)

        message = str(excinfo.value)
        assert f"Message is {len(body)} characters" in message
        assert f"signature adds {footer_length}" in message
        assert 'agent_signature = "off"' in message

    def test_a_body_that_still_fits_with_the_signature_passes(self) -> None:
        footer_length = len(f"\n\n_{self.FOOTER.mrkdwn()}_")
        body = "y" * (PLAIN_TEXT_LIMIT - footer_length)

        composed = compose_message(body, signature=self.FOOTER)

        assert len(composed.text) == PLAIN_TEXT_LIMIT
        assert composed.blocks is None

    def test_an_empty_body_is_not_signed(self) -> None:
        composed = compose_message("", signature=self.FOOTER)

        assert composed.blocks is None
        assert composed.text == ""
