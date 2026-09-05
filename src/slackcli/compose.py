"""Message composition rules shared by every command that writes to Slack.

The checks here run before any API call, so an over-long message is rejected
locally instead of being silently fragmented or refused by Slack.
"""

from __future__ import annotations

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
