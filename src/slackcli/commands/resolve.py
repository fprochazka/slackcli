"""Resolve command for Slack CLI - parse Slack URLs and fetch what they point at."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Literal
from urllib.parse import parse_qs, urlparse

import typer
from slack_sdk.errors import SlackApiError

from ..blocks import get_message_text
from ..context import get_context
from ..errors import format_error_with_hint
from ..logging import error_console, get_logger
from ..models import (
    Conversation,
    Message,
    ResolvedConversation,
    ResolvedFile,
    ResolvedMessage,
    ResolvedUser,
    resolve_slack_mentions,
)
from ..output import (
    output_resolved_conversation_json,
    output_resolved_conversation_text,
    output_resolved_file_json,
    output_resolved_file_text,
    output_resolved_message_json,
    output_resolved_message_text,
    output_resolved_user_json,
    output_resolved_user_text,
)

if TYPE_CHECKING:
    from ..client import SlackCli

logger = get_logger(__name__)

# Slack object ID prefixes: channels and DMs start with C, D or G, users with U or W.
CONVERSATION_ID = r"[CDG][A-Z0-9]+"
USER_ID = r"[UW][A-Z0-9]+"
FILE_ID = r"F[A-Z0-9]+"
TEAM_ID = r"T[A-Z0-9]+"

MESSAGE_PATH_RE = re.compile(rf"^/archives/({CONVERSATION_ID})/p(\d+)$")
CONVERSATION_PATH_RE = re.compile(rf"^/archives/({CONVERSATION_ID})$")
LEGACY_CONVERSATION_PATH_RE = re.compile(rf"^/messages/({CONVERSATION_ID})$")
USER_PATH_RE = re.compile(rf"^/team/({USER_ID})$")
FILE_PATH_RE = re.compile(rf"^/files/{USER_ID}/({FILE_ID})(?:/.*)?$")
CLIENT_CONVERSATION_PATH_RE = re.compile(rf"^/client/{TEAM_ID}/({CONVERSATION_ID})$")
CLIENT_THREAD_PATH_RE = re.compile(
    rf"^/client/{TEAM_ID}/{CONVERSATION_ID}/thread/({CONVERSATION_ID})-(\d+\.\d+)$",
)


@dataclass(frozen=True)
class ParsedSlackUrl:
    """Represents a parsed Slack URL.

    The ``kind`` field tells which fields carry data. A ``message`` has a
    channel ID and a message timestamp. A ``conversation`` has a channel ID
    only. A ``user`` has a user ID. A ``file`` has a file ID.
    """

    kind: Literal["message", "conversation", "user", "file"]
    workspace: str | None = None
    channel_id: str | None = None
    message_ts: str | None = None
    thread_ts: str | None = None
    is_thread_reply: bool = False
    user_id: str | None = None
    file_id: str | None = None


def _convert_url_timestamp(ts_without_dot: str) -> str:
    """Convert a URL timestamp to the Slack API format.

    Args:
        ts_without_dot: The digits after the ``p`` in the URL path.

    Returns:
        The timestamp with a dot before the last 6 digits, for example
        ``p1769432401438239`` becomes ``1769432401.438239``.

    Raises:
        ValueError: If the timestamp is too short.
    """
    if len(ts_without_dot) < 7:
        raise ValueError(f"Invalid timestamp in URL: {ts_without_dot}")

    return f"{ts_without_dot[:-6]}.{ts_without_dot[-6:]}"


def parse_slack_url(url: str) -> ParsedSlackUrl:
    """Parse a Slack URL and detect what it points at.

    URL formats:
    - Message: https://example.slack.com/archives/C09D1VBRJ76/p1769432401438239
    - Thread reply: https://example.slack.com/archives/C09D1VBRJ76/p1769422824936319?thread_ts=1769420875.054379&cid=C09D1VBRJ76
    - Conversation: https://example.slack.com/archives/C09D1VBRJ76
    - Conversation (legacy): https://example.slack.com/messages/C09D1VBRJ76
    - Conversation (web client): https://app.slack.com/client/T0123456/C09D1VBRJ76
    - Thread (web client): https://app.slack.com/client/T0123456/C09D1VBRJ76/thread/C09D1VBRJ76-1769420875.054379
    - User: https://example.slack.com/team/U08GTCPJW95
    - File: https://example.slack.com/files/U08GTCPJW95/F0123456789/report.pdf

    Args:
        url: The Slack URL.

    Returns:
        ParsedSlackUrl with extracted data.

    Raises:
        ValueError: If the URL format is invalid.
    """
    parsed = urlparse(url)

    # Validate hostname ends with slack.com
    if not parsed.hostname or not parsed.hostname.endswith("slack.com"):
        raise ValueError(f"Invalid Slack URL: hostname must end with slack.com, got '{parsed.hostname}'")

    # Extract workspace subdomain from hostname
    # e.g., "myworkspace.slack.com" -> "myworkspace"
    hostname_parts = parsed.hostname.split(".")
    if len(hostname_parts) < 3:
        raise ValueError(f"Invalid Slack URL hostname format: {parsed.hostname}")

    workspace = hostname_parts[0]
    path = parsed.path.rstrip("/")

    # Parse query params for thread info
    query_params = parse_qs(parsed.query)
    thread_ts_list = query_params.get("thread_ts", [])
    thread_ts = thread_ts_list[0] if thread_ts_list else None

    if match := MESSAGE_PATH_RE.match(path):
        message_ts = _convert_url_timestamp(match.group(2))
        # A message is a thread reply when thread_ts points at another message.
        is_thread_reply = thread_ts is not None and thread_ts != message_ts
        return ParsedSlackUrl(
            kind="message",
            workspace=workspace,
            channel_id=match.group(1),
            message_ts=message_ts,
            thread_ts=thread_ts,
            is_thread_reply=is_thread_reply,
        )

    if match := CONVERSATION_PATH_RE.match(path):
        # Without a message segment, thread_ts identifies the thread root.
        if thread_ts:
            return ParsedSlackUrl(
                kind="message",
                workspace=workspace,
                channel_id=match.group(1),
                message_ts=thread_ts,
                thread_ts=thread_ts,
            )
        return ParsedSlackUrl(kind="conversation", workspace=workspace, channel_id=match.group(1))

    if match := LEGACY_CONVERSATION_PATH_RE.match(path):
        return ParsedSlackUrl(kind="conversation", workspace=workspace, channel_id=match.group(1))

    if match := USER_PATH_RE.match(path):
        return ParsedSlackUrl(kind="user", workspace=workspace, user_id=match.group(1))

    if match := FILE_PATH_RE.match(path):
        return ParsedSlackUrl(kind="file", workspace=workspace, file_id=match.group(1))

    # Web client URLs live on app.slack.com, so the hostname names no workspace.
    if match := CLIENT_THREAD_PATH_RE.match(path):
        return ParsedSlackUrl(
            kind="message",
            channel_id=match.group(1),
            message_ts=match.group(2),
            thread_ts=match.group(2),
        )

    if match := CLIENT_CONVERSATION_PATH_RE.match(path):
        return ParsedSlackUrl(kind="conversation", channel_id=match.group(1))

    raise ValueError(f"Invalid Slack URL path format: {parsed.path}")


def get_channel_name_from_cache(slack: SlackCli, channel_id: str) -> str | None:
    """Get channel name from cache.

    Args:
        slack: The SlackCli client.
        channel_id: The channel ID.

    Returns:
        Channel name or None if not found.
    """
    conversations = slack.get_conversations_from_cache()
    if conversations is None:
        return None

    for convo in conversations:
        if convo.id == channel_id:
            return convo.name or None

    return None


def get_conversation(slack: SlackCli, channel_id: str) -> Conversation:
    """Get a conversation from the cache, or from the API on a cache miss.

    The API call makes conversations resolvable that the user is not a member of.

    Args:
        slack: The SlackCli client.
        channel_id: The conversation ID.

    Returns:
        The Conversation.

    Raises:
        SlackApiError: If the API call fails.
    """
    conversations = slack.get_conversations_from_cache()
    if conversations is not None:
        for convo in conversations:
            if convo.id == channel_id:
                return convo

    return Conversation.from_api(slack.get_conversation_info(channel_id))


def _resolve_message(slack: SlackCli, parsed: ParsedSlackUrl, output_json_flag: bool) -> None:
    """Fetch and print the message a URL points at."""
    channel_id = parsed.channel_id or ""
    message_ts = parsed.message_ts or ""

    # Get channel name from cache
    channel_name = get_channel_name_from_cache(slack, channel_id)
    if channel_name is None:
        channel_name = channel_id

    # Fetch the message
    if parsed.is_thread_reply and parsed.thread_ts:
        message_data = slack.get_thread_reply(channel_id, parsed.thread_ts, message_ts)
    else:
        message_data = slack.get_message(channel_id, message_ts)

    if message_data is None:
        error_console.print("[red]Message not found.[/red]")
        raise typer.Exit(1)

    # Collect user IDs for resolution
    user_ids: set[str] = set()
    if user_id := message_data.get("user"):
        user_ids.add(user_id)

    # Extract mentioned user IDs from text
    text = message_data.get("text", "")
    if text:
        mentioned_users = re.findall(r"<@([A-Z0-9]+)(?:\|[^>]*)?>", text)
        user_ids.update(mentioned_users)

    # Resolve user names
    users = slack.get_user_display_names(list(user_ids))

    # Get channel names from cache
    channels_map = slack.get_channel_names()

    # Convert to Message model
    message = Message.from_api(message_data, users, channels_map, get_message_text, resolve_slack_mentions)

    # Create resolved message output
    resolved = ResolvedMessage(
        channel_id=channel_id,
        channel_name=channel_name,
        message_ts=message_ts,
        thread_ts=parsed.thread_ts,
        is_thread_reply=parsed.is_thread_reply,
        message=message,
    )

    # Output
    if output_json_flag:
        output_resolved_message_json(resolved)
    else:
        output_resolved_message_text(resolved)


def _resolve_conversation(slack: SlackCli, parsed: ParsedSlackUrl, output_json_flag: bool) -> None:
    """Fetch and print the conversation a URL points at."""
    conversation = get_conversation(slack, parsed.channel_id or "")

    # A DM carries no name, so show the other member instead.
    user_name = None
    if conversation.is_im and conversation.user_id:
        user = slack.get_user(conversation.user_id)
        user_name = user.name if user else conversation.user_id

    resolved = ResolvedConversation(conversation=conversation, user_name=user_name)

    if output_json_flag:
        output_resolved_conversation_json(resolved)
    else:
        output_resolved_conversation_text(resolved)


def _resolve_user(slack: SlackCli, parsed: ParsedSlackUrl, output_json_flag: bool) -> None:
    """Fetch and print the user a URL points at."""
    user_id = parsed.user_id or ""
    user = slack.get_user(user_id)
    if user is None:
        error_console.print(f"[red]User not found: {user_id}[/red]")
        raise typer.Exit(1)

    resolved = ResolvedUser(user=user)

    if output_json_flag:
        output_resolved_user_json(resolved)
    else:
        output_resolved_user_text(resolved)


def _resolve_file(slack: SlackCli, parsed: ParsedSlackUrl, output_json_flag: bool) -> None:
    """Fetch and print the file a URL points at."""
    result = slack.get_file_info(parsed.file_id or "")
    resolved = ResolvedFile(file=result.get("file", {}))

    if output_json_flag:
        output_resolved_file_json(resolved)
    else:
        output_resolved_file_text(resolved)


def resolve_command(
    url: Annotated[
        str,
        typer.Argument(
            help="Slack URL to resolve.",
        ),
    ],
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output as JSON.",
        ),
    ] = False,
) -> None:
    """Resolve a Slack URL and fetch what it points at.

    Handles links to a message, a thread, a conversation, a user and a file.

    Examples:
        slack resolve 'https://example.slack.com/archives/C09D1VBRJ76/p1769432401438239'
        slack resolve 'https://example.slack.com/archives/C09D1VBRJ76/p1769422824936319?thread_ts=1769420875.054379&cid=C09D1VBRJ76'
        slack resolve 'https://example.slack.com/archives/C09D1VBRJ76'
        slack resolve 'https://example.slack.com/team/U08GTCPJW95'
        slack resolve 'https://example.slack.com/files/U08GTCPJW95/F0123456789/report.pdf'
        slack resolve 'https://app.slack.com/client/T0123456/C09D1VBRJ76'
        slack resolve 'https://example.slack.com/archives/C09D1VBRJ76/p1769432401438239' --json
    """
    # Parse the URL
    try:
        parsed = parse_slack_url(url)
    except ValueError as e:
        error_console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1) from None

    # Get org context
    ctx = get_context()

    # Use subdomain from URL as fallback if --org was not specified
    if ctx.org_name is None and parsed.workspace is not None:
        ctx.org_name = parsed.workspace

    try:
        slack = ctx.get_slack_client()
    except ValueError as e:
        error_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None

    try:
        if parsed.kind == "message":
            _resolve_message(slack, parsed, output_json_flag)
        elif parsed.kind == "conversation":
            _resolve_conversation(slack, parsed, output_json_flag)
        elif parsed.kind == "user":
            _resolve_user(slack, parsed, output_json_flag)
        else:
            _resolve_file(slack, parsed, output_json_flag)
    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")
        raise typer.Exit(1) from None
