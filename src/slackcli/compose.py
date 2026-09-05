"""Message composition rules shared by every command that writes to Slack.

The checks here run before any API call, so an over-long message is rejected
locally instead of being silently fragmented or refused by Slack.
"""

from __future__ import annotations

from typing import Any

# Slack splits a plain text message longer than this into several posts on
# chat.postMessage, and rejects it outright on chat.update (msg_too_long).
PLAIN_TEXT_LIMIT = 4000


class ComposeError(ValueError):
    """A message cannot be composed for Slack, with a hint on how to fix it."""


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
