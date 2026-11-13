"""Centralized error hints for Slack API errors."""

from __future__ import annotations

from typing import NoReturn

import typer
from slack_sdk.errors import SlackApiError

from .logging import error_console

# Exit code for a command that failed because Slack rate-limited a call (EX_TEMPFAIL from sysexits.h).
# Every other Slack API error exits with 1.
EXIT_RATE_LIMITED = 75

RATE_LIMIT_ERROR_CODES = frozenset({"ratelimited", "rate_limited"})
RATE_LIMIT_HINT = (
    "Slack kept rate-limiting this call after the automatic retries ran out. "
    "Wait at least the Retry-After time, then run the command again."
)

# Mapping of Slack API error codes to helpful hint messages
ERROR_HINTS: dict[str, str] = {
    # Authentication errors
    "invalid_auth": "Token is invalid or expired. Check your config at ~/.config/slackcli/config.toml.",
    "token_expired": "Token has expired. Generate a new token and update your config.",
    "token_revoked": "Token has been revoked. Generate a new token and update your config.",
    "not_authed": "No authentication token provided. Check your config at ~/.config/slackcli/config.toml.",
    "account_inactive": "The user account associated with the token is deactivated.",
    # Channel errors
    "not_in_channel": "The bot/user must be a member of this channel. Run /invite @yourbot in Slack.",
    "channel_not_found": (
        "Channel not found or you don't have access to it. "
        "The workspace in a Slack URL does not have to match your config alias. "
        "Try --org=<name> (see `slack config`)."
    ),
    "is_archived": "This channel is archived and cannot receive messages.",
    # Message errors
    "message_not_found": "The message with this timestamp was not found.",
    "cant_update_message": "You can only edit your own messages.",
    "cant_delete_message": "You can only delete your own messages, or you need admin privileges.",
    "edit_window_closed": "The edit window for this message has expired.",
    "msg_too_long": "Slack rejects message text over 4000 characters. Split the message, or shorten it.",
    "msg_blocks_too_long": (
        "Slack refuses rich-text content at roughly 13,000 characters per message, and this CLI checks at 12,000 "
        "to stay clear of it. Split the message into several."
    ),
    "no_text": "Message text cannot be empty.",
    "compliance_exports_prevent_deletion": "Compliance exports are enabled, preventing message deletion.",
    # Reaction errors
    "already_reacted": "You have already added this reaction to the message.",
    "no_reaction": "You haven't added this reaction to the message.",
    "invalid_name": "The emoji name is not valid.",
    "too_many_emoji": "The message has too many reactions.",
    "too_many_reactions": "The message has too many reactions.",
    # Pin errors
    "already_pinned": "This message is already pinned to the channel.",
    "no_pin": "This message is not pinned to the channel.",
    "not_pinnable": "This message type cannot be pinned.",
    "permission_denied": "You don't have permission to pin/unpin messages in this channel.",
    # Scheduled message errors
    "time_in_past": "The scheduled time must be in the future.",
    "time_too_far": "Cannot schedule a message more than 120 days in the future.",
    "invalid_scheduled_message_id": "The scheduled message ID is invalid or the message has already been posted.",
    "already_scheduled": "This message has already been scheduled.",
    "invalid_time": "The scheduled time is not valid.",
    # Rate limiting
    "ratelimited": RATE_LIMIT_HINT,
    "rate_limited": RATE_LIMIT_HINT,
    # Permission errors
    "missing_scope": "The token is missing required OAuth scopes. Check your Slack app at https://api.slack.com/apps.",
    "restricted_action": "This action is restricted by workspace admins.",
    "not_allowed_token_type": "This API method is not allowed for the token type.",
    "ekm_access_denied": "Access denied due to Enterprise Key Management.",
    # User errors
    "user_not_found": (
        "User not found. "
        "The workspace in a Slack URL does not have to match your config alias. "
        "Try --org=<name> (see `slack config`)."
    ),
    "user_not_visible": "The user is not visible to you.",
    # File errors
    "file_not_found": (
        "File not found or you don't have access to it. "
        "The workspace in a Slack URL does not have to match your config alias. "
        "Try --org=<name> (see `slack config`)."
    ),
    # Conversation membership errors
    "already_in_channel": "One or more users are already in the channel.",
    "cant_invite_self": "You cannot invite yourself to a channel.",
    "cant_invite": "One or more users cannot be invited to this channel.",
    "ura_max_channels": "A guest user has reached their channel limit.",
    "user_is_restricted": "A guest user is restricted from this conversation.",
    "no_external_invite_permission": "You don't have permission to invite external users here.",
    "method_not_supported_for_channel_type": (
        "This operation is not supported for this channel type "
        "(e.g. you cannot join private channels — ask to be invited)."
    ),
    "is_archived_for_channel": "The channel is archived.",
    # General errors
    "request_timeout": "The request timed out. Try again.",
    "service_unavailable": "Slack is temporarily unavailable. Try again later.",
    "fatal_error": "A server error occurred. Try again later.",
    "internal_error": "A server error occurred. Try again later.",
}


def get_error_hint(error_code: str) -> str | None:
    """Get a helpful hint message for a Slack API error code.

    Args:
        error_code: The Slack API error code (e.g., "not_in_channel").

    Returns:
        A helpful hint message, or None if no hint is available.
    """
    return ERROR_HINTS.get(error_code)


def get_error_code(error: SlackApiError) -> str:
    """Extract the error code from a SlackApiError.

    Args:
        error: The SlackApiError exception.

    Returns:
        The error code string.
    """
    return error.response.get("error", str(error))


def format_error_with_hint(error: SlackApiError, context: dict[str, str] | None = None) -> tuple[str, str | None]:
    """Format a SlackApiError with an optional hint.

    Args:
        error: The SlackApiError exception.
        context: Optional context dict for formatting hints (e.g., {"emoji": "thumbsup"}).

    Returns:
        A tuple of (error_message, hint_message or None).
    """
    error_code = get_error_code(error)
    hint = get_error_hint(error_code)

    # Handle special cases that need context
    if hint and context and error_code == "invalid_name" and "emoji" in context:
        hint = f"'{context['emoji']}' is not a valid emoji name."

    return f"Slack API error: {error_code}", hint


def get_retry_after(error: SlackApiError) -> str | None:
    """Read the Retry-After header from the response of a failed call.

    The header name is matched case-insensitively, because it arrives as "Retry-After" or "retry-after"
    depending on the HTTP client and protocol version.

    Args:
        error: The SlackApiError exception.

    Returns:
        The header value, or None when the response carries no Retry-After header.
    """
    headers = getattr(error.response, "headers", None) or {}
    for name, value in headers.items():
        if name.lower() == "retry-after":
            if isinstance(value, list):
                value = value[0] if value else None
            return str(value) if value is not None else None
    return None


def get_api_method(error: SlackApiError) -> str | None:
    """Name the Slack API method whose call failed, such as "conversations.history".

    Args:
        error: The SlackApiError exception.

    Returns:
        The method name taken from the request URL, or None when the response does not carry the URL.
    """
    api_url = getattr(error.response, "api_url", None)
    if not api_url:
        return None
    return api_url.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or None


def fail(error: SlackApiError, context: dict[str, str] | None = None, prefix: str = "") -> NoReturn:
    """Print a Slack API error and its hint on stderr, then exit the command.

    A rate-limit error also prints the Retry-After value and the API method on a line of its own,
    and exits with EXIT_RATE_LIMITED. Every other error exits with 1.

    Args:
        error: The SlackApiError raised by the API call.
        context: Optional context for formatting the hint, as in format_error_with_hint.
        prefix: Optional text printed in front of the error message, such as "Failed to open DM: ".

    Raises:
        typer.Exit: Always.
    """
    error_msg, hint = format_error_with_hint(error, context)
    error_console.print(f"[red]{prefix}{error_msg}[/red]")

    exit_code = 1
    if get_error_code(error) in RATE_LIMIT_ERROR_CODES:
        retry_after = get_retry_after(error)
        retry_text = f"{retry_after}s" if retry_after is not None else "unknown"
        method = get_api_method(error) or "unknown"
        # One plain, unwrapped line, so a script can parse it from stderr.
        retry_line = f"Retry-After: {retry_text} (method: {method})"
        error_console.print(retry_line, markup=False, highlight=False, soft_wrap=True)
        exit_code = EXIT_RATE_LIMITED

    if hint:
        error_console.print(f"[dim]Hint: {hint}[/dim]")
    raise typer.Exit(exit_code) from None
