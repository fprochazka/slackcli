"""Resolve command for Slack CLI - parse Slack URLs and fetch messages."""

import json as json_module
import re
from dataclasses import dataclass
from datetime import timezone
from typing import Annotated, Any
from urllib.parse import parse_qs, urlparse

import typer
from rich.console import Console
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..blocks import get_message_text
from ..cache import load_cache
from ..context import get_context
from ..logging import error_console, get_logger
from ..users import get_channel_names, get_user_display_names

console = Console()
logger = get_logger(__name__)

CONVERSATIONS_CACHE_NAME = "conversations"


@dataclass
class ParsedSlackUrl:
    """Represents a parsed Slack message URL."""

    channel_id: str
    message_ts: str
    thread_ts: str | None
    is_thread_reply: bool


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
    )


def get_channel_name_from_cache(org_name: str, channel_id: str) -> str | None:
    """Get channel name from cache.

    Args:
        org_name: The organization name.
        channel_id: The channel ID.

    Returns:
        Channel name or None if not found.
    """
    cache_data = load_cache(org_name, CONVERSATIONS_CACHE_NAME)
    if cache_data is None:
        return None

    data = cache_data.get("data", {})
    conversations = data.get("conversations", [])

    for convo in conversations:
        if convo.get("id") == channel_id:
            return convo.get("name")

    return None


def fetch_message(
    client: WebClient,
    channel_id: str,
    message_ts: str,
) -> dict[str, Any] | None:
    """Fetch a single message from a channel.

    Args:
        client: The Slack WebClient.
        channel_id: The channel ID.
        message_ts: The message timestamp.

    Returns:
        Message data or None if not found.
    """
    try:
        response = client.conversations_history(
            channel=channel_id,
            latest=message_ts,
            inclusive=True,
            limit=1,
        )

        if response["ok"]:
            messages = response.get("messages", [])
            if messages:
                return messages[0]
    except SlackApiError as e:
        logger.debug(f"Failed to fetch message: {e}")
        raise

    return None


def fetch_thread_reply(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    message_ts: str,
) -> dict[str, Any] | None:
    """Fetch a specific reply from a thread.

    Args:
        client: The Slack WebClient.
        channel_id: The channel ID.
        thread_ts: The parent thread timestamp.
        message_ts: The specific reply timestamp.

    Returns:
        Message data or None if not found.
    """
    try:
        response = client.conversations_replies(
            channel=channel_id,
            ts=thread_ts,
            latest=message_ts,
            inclusive=True,
            limit=1,
        )

        if response["ok"]:
            messages = response.get("messages", [])
            # Find the exact message by ts
            for msg in messages:
                if msg.get("ts") == message_ts:
                    return msg
    except SlackApiError as e:
        logger.debug(f"Failed to fetch thread reply: {e}")
        raise

    return None


def slack_ts_to_datetime_str(ts: str) -> str:
    """Convert Slack timestamp to datetime string.

    Args:
        ts: Slack timestamp.

    Returns:
        Formatted datetime string.
    """
    from datetime import datetime

    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        return ts


def resolve_slack_mentions(text: str, users: dict[str, str], channels: dict[str, str]) -> str:
    """Replace Slack mention macros with readable names.

    Args:
        text: The original message text.
        users: Dictionary mapping user ID to username.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Text with mentions replaced.
    """
    if not text:
        return text

    # Replace user mentions: <@U08GTCPJW95> or <@U08GTCPJW95|display_name>
    def replace_user_mention(match: re.Match) -> str:
        user_id = match.group(1)
        username = users.get(user_id, user_id)
        return f"@{username}"

    text = re.sub(r"<@([A-Z0-9]+)(?:\|[^>]*)?>", replace_user_mention, text)

    # Replace channel mentions: <#C01234567> or <#C01234567|channel-name>
    def replace_channel_mention(match: re.Match) -> str:
        channel_id = match.group(1)
        channel_name_in_mention = match.group(2)
        if channel_name_in_mention:
            return f"#{channel_name_in_mention}"
        channel_name = channels.get(channel_id, channel_id)
        return f"#{channel_name}"

    text = re.sub(r"<#([A-Z0-9]+)(?:\|([^>]*))?>", replace_channel_mention, text)

    # Replace links: <https://example.com|link text> or <https://example.com>
    def replace_link(match: re.Match) -> str:
        url = match.group(1)
        link_text = match.group(2)
        if link_text:
            return f"{link_text} ({url})"
        return url

    text = re.sub(r"<(https?://[^|>]+)(?:\|([^>]*))?>", replace_link, text)

    # Replace special mentions
    text = re.sub(r"<!here>", "@here", text)
    text = re.sub(r"<!channel>", "@channel", text)
    text = re.sub(r"<!everyone>", "@everyone", text)

    return text


def format_message_output(
    message: dict[str, Any],
    users: dict[str, str],
    channels_map: dict[str, str],
) -> str:
    """Format message text with indentation.

    Args:
        message: Message data from API.
        users: Dictionary mapping user ID to username.
        channels_map: Dictionary mapping channel ID to channel name.

    Returns:
        Formatted message text.
    """
    text = get_message_text(message, users, channels_map)
    text = resolve_slack_mentions(text, users, channels_map)

    if not text:
        return "  (no text)"

    lines = text.split("\n")
    return "\n".join(f"  {line}" for line in lines)


def resolve_command(
    url: Annotated[
        str,
        typer.Argument(
            help="Slack message URL to resolve.",
        ),
    ],
    output_json: Annotated[
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
    org = ctx.get_org()
    org_name = org.name

    # Get channel name from cache
    channel_name = get_channel_name_from_cache(org_name, parsed.channel_id)
    if channel_name is None:
        channel_name = parsed.channel_id

    # Create Slack client
    client = WebClient(token=org.token)

    # Fetch the message
    try:
        if parsed.is_thread_reply and parsed.thread_ts:
            message = fetch_thread_reply(
                client,
                parsed.channel_id,
                parsed.thread_ts,
                parsed.message_ts,
            )
        else:
            message = fetch_message(client, parsed.channel_id, parsed.message_ts)
    except SlackApiError as e:
        error_console.print(f"[red]Slack API error: {e.response.get('error', str(e))}[/red]")
        raise typer.Exit(1) from None

    if message is None:
        error_console.print("[red]Message not found.[/red]")
        raise typer.Exit(1)

    # Collect user IDs for resolution
    user_ids: set[str] = set()
    if user_id := message.get("user"):
        user_ids.add(user_id)

    # Extract mentioned user IDs from text
    text = message.get("text", "")
    if text:
        mentioned_users = re.findall(r"<@([A-Z0-9]+)(?:\|[^>]*)?>", text)
        user_ids.update(mentioned_users)

    # Resolve user names
    users = get_user_display_names(client, org_name, list(user_ids))

    # Get channel names from cache
    channels_map = get_channel_names(org_name)

    if output_json:
        # JSON output
        user_id = message.get("user", "")
        user_name = users.get(user_id, user_id) if user_id else None

        text = get_message_text(message, users, channels_map)
        resolved_text = resolve_slack_mentions(text, users, channels_map)

        output = {
            "channel_id": parsed.channel_id,
            "channel_name": channel_name,
            "message_ts": parsed.message_ts,
            "thread_ts": parsed.thread_ts,
            "is_thread_reply": parsed.is_thread_reply,
            "message": {
                "ts": message.get("ts"),
                "user_id": user_id,
                "user_name": user_name,
                "text": resolved_text,
                "reactions": message.get("reactions", []),
            },
        }

        print(json_module.dumps(output, indent=2, ensure_ascii=False))
    else:
        # Human-readable output
        print(f"Channel: #{channel_name} ({parsed.channel_id})")

        if parsed.is_thread_reply and parsed.thread_ts:
            print(f"Thread: {parsed.thread_ts}")
            print(f"  To view full thread: slack messages '#{channel_name}' {parsed.thread_ts}")
            print(f"Message: {parsed.message_ts} (reply in thread)")
        else:
            print(f"Message: {parsed.message_ts}")

        print()

        # Format timestamp
        ts = message.get("ts", "")
        time_str = slack_ts_to_datetime_str(ts)

        # Get user name
        user_id = message.get("user", "")
        user_name = users.get(user_id, user_id) if user_id else "(unknown)"
        if user_name and not user_name.startswith("@"):
            user_name = f"@{user_name}"

        print(f"{time_str}  {user_name}")
        print(format_message_output(message, users, channels_map))
