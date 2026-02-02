"""Centralized error hints for Slack API errors."""

from __future__ import annotations

from slack_sdk.errors import SlackApiError

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
    "channel_not_found": "Channel not found or you don't have access to it.",
    "is_archived": "This channel is archived and cannot receive messages.",
    # Message errors
    "message_not_found": "The message with this timestamp was not found.",
    "cant_update_message": "You can only edit your own messages.",
    "cant_delete_message": "You can only delete your own messages, or you need admin privileges.",
    "edit_window_closed": "The edit window for this message has expired.",
    "msg_too_long": "Message exceeds Slack's 40,000 character limit.",
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
    # Rate limiting
    "ratelimited": "Rate limit exceeded. The request will be retried automatically.",
    "rate_limited": "Rate limit exceeded. The request will be retried automatically.",
    # Permission errors
    "missing_scope": "The token is missing required OAuth scopes. Update your Slack app permissions.",
    "restricted_action": "This action is restricted by workspace admins.",
    "not_allowed_token_type": "This API method is not allowed for the token type.",
    "ekm_access_denied": "Access denied due to Enterprise Key Management.",
    # User errors
    "user_not_found": "User not found.",
    "user_not_visible": "The user is not visible to you.",
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

    # Handle rate limit with Retry-After header
    if error_code in ("ratelimited", "rate_limited"):
        retry_after = error.response.headers.get("Retry-After", "unknown")
        hint = f"Rate limit exceeded. Try again in {retry_after} seconds."

    return f"Slack API error: {error_code}", hint
