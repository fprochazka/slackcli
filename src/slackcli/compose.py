"""Message composition rules shared by every command that writes to Slack.

The checks here run before any API call, so an over-long message is rejected
locally instead of being silently fragmented or refused by Slack.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .blocks import render_blocks

# Slack splits a plain text message longer than this into several posts on
# chat.postMessage, and rejects it outright on chat.update (msg_too_long).
PLAIN_TEXT_LIMIT = 4000

# Slack rejects a message with more blocks than this (invalid_blocks).
MAX_BLOCKS = 50

# Longest text a section or context block accepts (invalid_blocks above it).
SECTION_TEXT_LIMIT = 3000

# Rich text is capped per message, not per block, at roughly 13,000 characters
# (msg_blocks_too_long). Kept conservative so a message near the edge still posts.
RICH_TEXT_LIMIT = 12_000


class ComposeError(ValueError):
    """A message cannot be composed for Slack, with a hint on how to fix it."""


@dataclass
class ComposedMessage:
    """A message body turned into what the Slack API expects.

    Attributes:
        text: The message text, which is the fallback Slack shows in notifications,
            search results and plain clients when the message also carries blocks.
        blocks: The Block Kit blocks, or None for a plain text message.
        format: How the body was composed, one of "mrkdwn" or "blocks".
    """

    text: str
    blocks: list[dict[str, Any]] | None
    format: str


def check_plain_text_length(text: str) -> None:
    """Check that a plain text message fits into a single Slack message.

    Args:
        text: The message text as it will be sent.

    Raises:
        ComposeError: If the text is longer than PLAIN_TEXT_LIMIT characters.
    """
    if len(text) <= PLAIN_TEXT_LIMIT:
        return

    raise ComposeError(
        f"Message is {len(text)} characters. Slack splits plain messages over {PLAIN_TEXT_LIMIT} characters "
        "into several posts and returns only the last part's ts, which breaks formatting and threading. "
        "Split it into several shorter messages (for example a thread) or shorten it."
    )


def preview_removal_update(message: dict[str, Any]) -> dict[str, Any]:
    """Decide how to re-post a message's own content while dropping its link previews.

    Link previews live in the message's attachments, and the only way to remove them is
    a chat.update that sends an empty attachment list. Such an update has to carry the
    content again, so it is taken from the message as it stands. Blocks are what Slack
    stores for a rich message, so they are re-posted when present, together with the
    stored text, which Slack uses for notifications and search. That fallback text is
    dropped only when it is over 4000 characters, because chat.update rejects a longer
    text with msg_too_long while blocks have no such limit.

    Args:
        message: The message as returned by the Slack API.

    Returns:
        Keyword arguments for SlackCli.edit_message(), carrying blocks and/or text plus
        clear_attachments.

    Raises:
        ComposeError: If the message has no content of its own to re-post, or if it has
            no blocks and its text is too long for chat.update.
    """
    text = message.get("text", "")

    blocks = message.get("blocks") or []
    if blocks:
        update: dict[str, Any] = {"blocks": blocks, "clear_attachments": True}
        if len(text) <= PLAIN_TEXT_LIMIT:
            update["text"] = text
        return update

    if not text.strip():
        raise ComposeError(
            "This message has no text or blocks of its own, only attachments, so removing its link "
            "previews would leave it empty. Edit it with a new body instead."
        )

    check_plain_text_length(text)
    return {"text": text, "clear_attachments": True}


def load_blocks(path: str) -> list[dict[str, Any]]:
    """Read Block Kit blocks from a JSON file or from stdin.

    Both shapes the Block Kit Builder produces are accepted: a bare array of blocks, and
    an object with a "blocks" key holding that array.

    Args:
        path: Path to the JSON file, or "-" to read stdin.

    Returns:
        The list of blocks, unvalidated.

    Raises:
        ComposeError: If the input cannot be read, is not JSON, or holds neither an
            array of blocks nor an object with a "blocks" array.
    """
    if path == "-":
        if sys.stdin.isatty():
            raise ComposeError("--blocks - reads the blocks from stdin, but nothing was piped in.")
        raw = sys.stdin.read()
        if not raw.strip():
            raise ComposeError("No blocks were read from stdin.")
        source = "stdin"
    else:
        try:
            raw = Path(path).read_text()
        except OSError as e:
            raise ComposeError(f"Cannot read blocks from {path}: {e.strerror or e}.") from None
        source = path

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ComposeError(f"Blocks from {source} are not valid JSON: {e}.") from None

    if isinstance(data, dict):
        data = data.get("blocks")
        if data is None:
            raise ComposeError(
                f"Blocks from {source} are a JSON object without a 'blocks' key. "
                "Pass an array of blocks, or a Block Kit Builder export."
            )

    if not isinstance(data, list):
        raise ComposeError(
            f"Blocks from {source} are {type(data).__name__}, not an array of blocks. "
            "Pass an array of blocks, or an object with a 'blocks' array."
        )

    return data


def _rich_text_length(element: Any) -> int:
    """Sum the length of every text value inside a rich text element tree.

    Args:
        element: A block, element or list of them from a rich_text block.

    Returns:
        The total number of characters of text the element carries.
    """
    if isinstance(element, list):
        return sum(_rich_text_length(item) for item in element)

    if not isinstance(element, dict):
        return 0

    total = 0
    text = element.get("text")
    if isinstance(text, str):
        total += len(text)
    for key in ("elements", "element"):
        if key in element:
            total += _rich_text_length(element[key])
    return total


def _block_text_lengths(block: dict[str, Any]) -> list[int]:
    """Collect the length of every text field of a section or context block.

    Args:
        block: A section or context block.

    Returns:
        The lengths of the texts the block carries.
    """
    lengths = []

    text = block.get("text")
    if isinstance(text, dict) and isinstance(text.get("text"), str):
        lengths.append(len(text["text"]))

    for field in block.get("fields", []) or []:
        if isinstance(field, dict) and isinstance(field.get("text"), str):
            lengths.append(len(field["text"]))

    for element in block.get("elements", []) or []:
        if isinstance(element, dict) and isinstance(element.get("text"), str):
            lengths.append(len(element["text"]))

    return lengths


def check_blocks(blocks: list[dict[str, Any]]) -> None:
    """Check that a list of blocks is shaped like Block Kit and fits Slack's limits.

    Args:
        blocks: The blocks as they will be sent.

    Raises:
        ComposeError: If the blocks are malformed or exceed a Slack limit.
    """
    if not blocks:
        raise ComposeError("No blocks to send: the block list is empty.")

    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            raise ComposeError(f"Block {index} is {type(block).__name__}, not an object with a 'type'.")
        if not isinstance(block.get("type"), str):
            raise ComposeError(f"Block {index} has no 'type'. Every Block Kit block needs one.")

    if len(blocks) > MAX_BLOCKS:
        raise ComposeError(
            f"Message has {len(blocks)} blocks. Slack accepts at most {MAX_BLOCKS} per message. "
            "Split it into several messages."
        )

    for index, block in enumerate(blocks):
        block_type = block["type"]

        if block_type in ("section", "context"):
            for length in _block_text_lengths(block):
                if length > SECTION_TEXT_LIMIT:
                    raise ComposeError(
                        f"Block {index} ({block_type}) has {length} characters of text. Slack accepts at most "
                        f"{SECTION_TEXT_LIMIT} per section or context block. Split it across several blocks."
                    )

    rich_text_total = sum(_rich_text_length(block) for block in blocks if block["type"] == "rich_text")
    if rich_text_total > RICH_TEXT_LIMIT:
        raise ComposeError(
            f"Message carries {rich_text_total} characters of rich text. Slack accepts roughly "
            f"{RICH_TEXT_LIMIT} per message across all rich_text blocks. Split it into several messages."
        )


def compose_message(body: str | None, *, blocks: list[dict[str, Any]] | None = None) -> ComposedMessage:
    """Turn a message body into the text and blocks to hand to the Slack API.

    A message that carries blocks always carries a fallback text as well, because that
    is what Slack shows in notifications, in search results and in clients that cannot
    render blocks. When no body is given, or the body is blank, the fallback is rendered
    from the blocks and shortened if that rendering is longer than a message may be.

    Args:
        body: The message text, or None when the content comes from blocks alone.
        blocks: Block Kit blocks to send instead of plain text.

    Returns:
        The composed message.

    Raises:
        ComposeError: If the body is too long, or the blocks are malformed or exceed a
            Slack limit.
    """
    if blocks is not None:
        check_blocks(blocks)

        # A derived fallback is shortened to fit; one the caller wrote is rejected instead,
        # because silently cutting text somebody typed would hide part of their message.
        if body is None or not body.strip():
            text = render_blocks(blocks, {}, {})
            if len(text) > PLAIN_TEXT_LIMIT:
                text = text[: PLAIN_TEXT_LIMIT - 1] + "…"
        elif len(body) > PLAIN_TEXT_LIMIT:
            raise ComposeError(
                f"Fallback text is {len(body)} characters. Slack caps the text of a message at "
                f"{PLAIN_TEXT_LIMIT} characters even when blocks carry the content. Shorten it, or leave it "
                "out so it is derived from the blocks."
            )
        else:
            text = body

        return ComposedMessage(text=text, blocks=blocks, format="blocks")

    text = body or ""
    check_plain_text_length(text)
    return ComposedMessage(text=text, blocks=None, format="mrkdwn")
