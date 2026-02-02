"""Messages command for Slack CLI."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Annotated, Any

import typer
from slack_sdk.errors import SlackApiError

from ..blocks import get_message_text
from ..context import get_context
from ..logging import console, error_console, get_logger
from ..models import Message, MessagesOutput, resolve_slack_mentions
from ..output import (
    output_messages_json,
    output_messages_text,
    output_thread_text,
)

if TYPE_CHECKING:
    from ..client import SlackCli

logger = get_logger(__name__)


def parse_time_spec(spec: str) -> datetime:
    """Parse a time specification into a datetime.

    Supports:
    - ISO date: "2024-01-15"
    - Relative: "7d", "1h", "2w", "30m"
    - Keywords: "today", "yesterday"

    Args:
        spec: The time specification string.

    Returns:
        Parsed datetime.

    Raises:
        ValueError: If spec cannot be parsed.
    """
    spec = spec.strip().lower()
    now = datetime.now(tz=timezone.utc)

    # Keywords
    if spec == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if spec == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if spec == "now":
        return now

    # Relative time: 7d, 1h, 2w, 30m
    relative_match = re.match(r"^(\d+)([hdwm])$", spec)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2)
        if unit == "h":
            return now - timedelta(hours=amount)
        if unit == "d":
            return now - timedelta(days=amount)
        if unit == "w":
            return now - timedelta(weeks=amount)
        if unit == "m":
            return now - timedelta(minutes=amount)

    # ISO date: 2024-01-15 or 2024-01-15T10:30:00
    try:
        # Try datetime with time
        if "T" in spec or " " in spec:
            dt = datetime.fromisoformat(spec.replace(" ", "T"))
        else:
            # Just date, start of day
            dt = datetime.fromisoformat(spec)
            dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        # If no timezone, assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        pass

    raise ValueError(f"Cannot parse time specification: {spec}")


def resolve_channel(slack: SlackCli, channel_ref: str) -> tuple[str, str]:
    """Resolve a channel reference to a channel ID and name.

    Args:
        slack: The SlackCli client.
        channel_ref: Channel reference - either '#channel-name' or raw ID.

    Returns:
        Tuple of (channel_id, channel_name).

    Raises:
        typer.Exit: If channel cannot be resolved.
    """
    # Load conversations from cache (no API call, just read cache)
    conversations = slack.get_conversations_from_cache()
    if conversations is None:
        error_console.print("[red]Conversations cache not found. Run 'slack conversations list' first.[/red]")
        raise typer.Exit(1)

    # If it's already a channel ID (starts with C, D, or G and is alphanumeric)
    if re.match(r"^[CDG][A-Z0-9]+$", channel_ref):
        # Look up the name from cache
        for convo in conversations:
            if convo.id == channel_ref:
                return channel_ref, convo.name or channel_ref
        # ID not found in cache, return ID as name
        return channel_ref, channel_ref

    # Strip # prefix if present
    channel_name = channel_ref.lstrip("#")

    # Search for matching channel
    for convo in conversations:
        if convo.name == channel_name:
            return convo.id, channel_name

    # Not found
    error_console.print(f"[red]Channel '{channel_ref}' not found in cache.[/red]")
    error_console.print("[dim]Run 'slack conversations list --refresh' to update the cache.[/dim]")
    raise typer.Exit(1)


def collect_user_ids_from_messages(
    messages: list[dict[str, Any]],
    include_reaction_users: bool = False,
    with_threads: bool = False,
) -> set[str]:
    """Collect all user IDs from messages for resolution.

    Args:
        messages: List of raw message data from API.
        include_reaction_users: Whether to include users from reactions.
        with_threads: Whether to include users from thread replies.

    Returns:
        Set of user IDs.
    """
    user_ids: set[str] = set()

    for msg in messages:
        if user_id := msg.get("user"):
            user_ids.add(user_id)

        # Collect user IDs from reactions
        if include_reaction_users:
            for reaction in msg.get("reactions", []):
                user_ids.update(reaction.get("users", []))

        # Collect user IDs from mentions in message text
        text = msg.get("text", "")
        if text:
            mentioned_users = re.findall(r"<@([A-Z0-9]+)(?:\|[^>]*)?>", text)
            user_ids.update(mentioned_users)

        # Collect user IDs from thread replies
        if with_threads:
            for reply in msg.get("replies", []):
                if reply_user_id := reply.get("user"):
                    user_ids.add(reply_user_id)

                if include_reaction_users:
                    for reaction in reply.get("reactions", []):
                        user_ids.update(reaction.get("users", []))

                reply_text = reply.get("text", "")
                if reply_text:
                    mentioned_users = re.findall(r"<@([A-Z0-9]+)(?:\|[^>]*)?>", reply_text)
                    user_ids.update(mentioned_users)

    return user_ids


def convert_messages_to_model(
    raw_messages: list[dict[str, Any]],
    users: dict[str, str],
    channels: dict[str, str],
    channel_id: str,
    channel_name: str,
) -> MessagesOutput:
    """Convert raw API messages to model objects.

    Args:
        raw_messages: List of raw message data from API.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.
        channel_id: The channel ID.
        channel_name: The channel name.

    Returns:
        MessagesOutput with converted messages.
    """
    # Messages come in reverse chronological order, reverse for display
    messages = [
        Message.from_api(msg, users, channels, get_message_text, resolve_slack_mentions)
        for msg in reversed(raw_messages)
    ]

    return MessagesOutput(
        channel_id=channel_id,
        channel_name=channel_name,
        messages=messages,
    )


def messages_command(
    channel: Annotated[
        str,
        typer.Argument(
            help="Channel reference (#channel-name or channel ID).",
        ),
    ],
    thread_ts: Annotated[
        str | None,
        typer.Argument(
            help="Thread timestamp to show replies for.",
        ),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            help="Start time (ISO date, relative like '7d', '1h', '2w', or 'today', 'yesterday').",
        ),
    ] = None,
    until: Annotated[
        str | None,
        typer.Option(
            "--until",
            help="End time (same format as --since, default: now).",
        ),
    ] = None,
    today: Annotated[
        bool,
        typer.Option(
            "--today",
            help="Shortcut for today's messages.",
        ),
    ] = False,
    last_7d: Annotated[
        bool,
        typer.Option(
            "--last-7d",
            help="Shortcut for last 7 days.",
        ),
    ] = False,
    last_30d: Annotated[
        bool,
        typer.Option(
            "--last-30d",
            help="Shortcut for last 30 days.",
        ),
    ] = False,
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            "-n",
            help="Maximum number of messages to fetch.",
        ),
    ] = 100,
    reactions: Annotated[
        str,
        typer.Option(
            "--reactions",
            help="How to show reactions: 'off', 'counts', or 'names'.",
        ),
    ] = "off",
    output_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output raw JSON instead of formatted text.",
        ),
    ] = False,
    with_threads: Annotated[
        bool,
        typer.Option(
            "--with-threads",
            help="Fetch and display thread replies for messages with threads.",
        ),
    ] = False,
) -> None:
    """List messages in a channel or thread.

    Examples:
        slack messages '#general'
        slack messages '#general' --since=7d
        slack messages '#general' --today
        slack messages '#general' 1234567890.123456  # thread replies
        slack messages C0123456789 --reactions=counts
    """
    # Validate reactions option
    if reactions not in ("off", "counts", "names"):
        error_console.print(f"[red]Invalid --reactions value: {reactions}. Use 'off', 'counts', or 'names'.[/red]")
        raise typer.Exit(1)

    # Handle time shortcuts
    if today:
        since = "today"
    elif last_7d:
        since = "7d"
    elif last_30d:
        since = "30d"

    # Parse time filters
    oldest: datetime | None = None
    latest: datetime | None = None

    # For channel messages (no thread_ts), default to last 30 days
    if thread_ts is None and since is None:
        since = "30d"

    if since:
        try:
            oldest = parse_time_spec(since)
        except ValueError as e:
            error_console.print(f"[red]Invalid --since value: {e}[/red]")
            raise typer.Exit(1) from None

    if until:
        try:
            latest = parse_time_spec(until)
        except ValueError as e:
            error_console.print(f"[red]Invalid --until value: {e}[/red]")
            raise typer.Exit(1) from None

    # Get org context
    cli_ctx = get_context()
    slack = cli_ctx.get_slack_client()

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # Fetch messages
    try:
        if thread_ts:
            if not output_json:
                console.print(f"[dim]Fetching thread replies for {thread_ts}...[/dim]")
            fetched_messages = slack.get_thread_replies(channel_id, thread_ts, limit)
        else:
            time_range = ""
            if oldest:
                time_range = f" from {oldest.strftime('%Y-%m-%d %H:%M')}"
            if latest:
                time_range += f" to {latest.strftime('%Y-%m-%d %H:%M')}"
            if not output_json:
                console.print(f"[dim]Fetching messages{time_range}...[/dim]")
            fetched_messages = slack.get_messages(channel_id, oldest, latest, limit)
    except SlackApiError as e:
        error_console.print(f"[red]Slack API error: {e.response.get('error', str(e))}[/red]")
        raise typer.Exit(1) from None

    if not fetched_messages:
        if output_json:
            output = MessagesOutput(
                channel_id=channel_id,
                channel_name=channel_name,
                messages=[],
            )
            output_messages_json(output, with_threads)
        else:
            console.print("[yellow]No messages found.[/yellow]")
        return

    if not output_json:
        console.print(f"[dim]Found {len(fetched_messages)} messages[/dim]\n")

    # Fetch thread replies if --with-threads is enabled and not already viewing a thread
    if with_threads and thread_ts is None:
        # Count messages with threads
        messages_with_threads = [msg for msg in fetched_messages if msg.get("reply_count", 0) > 0]
        if messages_with_threads and not output_json:
            console.print(f"[dim]Fetching {len(messages_with_threads)} threads...[/dim]")

        for msg in fetched_messages:
            reply_count = msg.get("reply_count", 0)
            if reply_count > 0:
                msg_ts = msg.get("ts", "")
                try:
                    thread_messages = slack.get_thread_replies(channel_id, msg_ts, reply_count + 1)
                    # Skip the first message (parent) to avoid duplication
                    if thread_messages:
                        msg["replies"] = thread_messages[1:]
                except SlackApiError as e:
                    logger.debug(f"Failed to fetch thread {msg_ts}: {e}")

    # Collect user IDs for resolution
    include_reaction_users = reactions == "names" or output_json
    user_ids = collect_user_ids_from_messages(
        fetched_messages,
        include_reaction_users=include_reaction_users,
        with_threads=with_threads,
    )

    # Resolve user names
    users = slack.get_user_display_names(list(user_ids))

    # Get channel names from cache for mention resolution
    channels = slack.get_channel_names()

    # Convert to model
    messages_output = convert_messages_to_model(fetched_messages, users, channels, channel_id, channel_name)

    # Output messages
    if output_json:
        output_messages_json(messages_output, with_threads)
    elif thread_ts:
        # For thread view, pass the raw Message list to thread display
        output_thread_text(messages_output.messages, reactions)
    else:
        output_messages_text(messages_output, reactions, with_threads)
