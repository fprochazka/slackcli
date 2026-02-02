"""Resolve command for Slack CLI - parse Slack URLs and fetch messages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated
from urllib.parse import parse_qs, urlparse

import typer
from slack_sdk.errors import SlackApiError

from ..blocks import get_message_text
from ..context import get_context
from ..logging import error_console, get_logger
from ..models import Message, ResolvedMessage, resolve_slack_mentions
from ..output import output_resolved_message_json, output_resolved_message_text

if TYPE_CHECKING:
    from ..client import SlackCli

logger = get_logger(__name__)


@dataclass
class ParsedSlackUrl:
    """Represents a parsed Slack message URL."""

    channel_id: str
    message_ts: str
    thread_ts: str | None
    is_thread_reply: bool
    workspace: str


def parse_slack_url(url: str) -> ParsedSlackUrl:
    """Parse a Slack message URL to extract channel ID and timestamps.

    URL formats:
    - Regular message: https://example.slack.com/archives/C09D1VBRJ76/p1769432401438239
    - Thread reply: https://example.slack.com/archives/C09D1VBRJ76/p1769422824936319?thread_ts=1769420875.054379&cid=C09D1VBRJ76

    Args:
        url: The Slack message URL.

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

    # Parse path: /archives/<channel_id>/p<timestamp>
    path_match = re.match(r"^/archives/([A-Z0-9]+)/p(\d+)$", parsed.path)
    if not path_match:
        raise ValueError(f"Invalid Slack URL path format: {parsed.path}")

    channel_id = path_match.group(1)
    ts_without_dot = path_match.group(2)

    # Convert timestamp: insert dot before last 6 digits
    # e.g., p1769432401438239 -> 1769432401.438239
    if len(ts_without_dot) < 7:
        raise ValueError(f"Invalid timestamp in URL: {ts_without_dot}")

    message_ts = f"{ts_without_dot[:-6]}.{ts_without_dot[-6:]}"

    # Parse query params for thread info
    query_params = parse_qs(parsed.query)
    thread_ts_list = query_params.get("thread_ts", [])
    thread_ts = thread_ts_list[0] if thread_ts_list else None

    # Determine if this is a thread reply
    # A message is a thread reply if:
    # 1. thread_ts is present in query params
    # 2. thread_ts is different from message_ts (pointing to parent)
    is_thread_reply = thread_ts is not None and thread_ts != message_ts

    return ParsedSlackUrl(
        channel_id=channel_id,
        message_ts=message_ts,
        thread_ts=thread_ts,
        is_thread_reply=is_thread_reply,
        workspace=workspace,
    )


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


def resolve_command(
    url: Annotated[
        str,
        typer.Argument(
            help="Slack message URL to resolve.",
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
    """Resolve a Slack message URL and fetch the message with channel info.

    Examples:
        slack resolve 'https://example.slack.com/archives/C09D1VBRJ76/p1769432401438239'
        slack resolve 'https://example.slack.com/archives/C09D1VBRJ76/p1769422824936319?thread_ts=1769420875.054379&cid=C09D1VBRJ76'
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
    if ctx.org_name is None:
        ctx.org_name = parsed.workspace

    try:
        slack = ctx.get_slack_client()
    except ValueError as e:
        error_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None

    # Get channel name from cache
    channel_name = get_channel_name_from_cache(slack, parsed.channel_id)
    if channel_name is None:
        channel_name = parsed.channel_id

    # Fetch the message
    try:
        if parsed.is_thread_reply and parsed.thread_ts:
            message_data = slack.get_thread_reply(
                parsed.channel_id,
                parsed.thread_ts,
                parsed.message_ts,
            )
        else:
            message_data = slack.get_message(parsed.channel_id, parsed.message_ts)
    except SlackApiError as e:
        error_console.print(f"[red]Slack API error: {e.response.get('error', str(e))}[/red]")
        raise typer.Exit(1) from None

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
        channel_id=parsed.channel_id,
        channel_name=channel_name,
        message_ts=parsed.message_ts,
        thread_ts=parsed.thread_ts,
        is_thread_reply=parsed.is_thread_reply,
        message=message,
    )

    # Output
    if output_json_flag:
        output_resolved_message_json(resolved)
    else:
        output_resolved_message_text(resolved)
