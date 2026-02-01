"""Messages command for Slack CLI."""

import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import typer
from rich.console import Console
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..cache import load_cache
from ..context import get_context
from ..logging import error_console, get_logger
from ..users import get_user_display_names

console = Console()
logger = get_logger(__name__)

CONVERSATIONS_CACHE_NAME = "conversations"


def datetime_to_slack_ts(dt: datetime) -> str:
    """Convert a datetime to a Slack timestamp string.

    Args:
        dt: The datetime to convert.

    Returns:
        Slack timestamp string.
    """
    return f"{dt.timestamp():.6f}"


def slack_ts_to_datetime(ts: str) -> datetime:
    """Convert a Slack timestamp to datetime.

    Args:
        ts: The Slack timestamp.

    Returns:
        Datetime object.
    """
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


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


def resolve_channel(org_name: str, channel_ref: str) -> str:
    """Resolve a channel reference to a channel ID.

    Args:
        org_name: The organization name for cache lookup.
        channel_ref: Channel reference - either '#channel-name' or raw ID.

    Returns:
        The channel ID.

    Raises:
        typer.Exit: If channel cannot be resolved.
    """
    # If it's already a channel ID (starts with C, D, or G and is alphanumeric)
    if re.match(r"^[CDG][A-Z0-9]+$", channel_ref):
        return channel_ref

    # Strip # prefix if present
    channel_name = channel_ref.lstrip("#")

    # Load conversations cache
    cache_data = load_cache(org_name, CONVERSATIONS_CACHE_NAME)
    if cache_data is None:
        error_console.print("[red]Conversations cache not found. Run 'slack convos list' first.[/red]")
        raise typer.Exit(1)

    data = cache_data.get("data", {})
    conversations = data.get("conversations", [])

    # Search for matching channel
    for convo in conversations:
        if convo.get("name") == channel_name:
            return convo.get("id")

    # Not found
    error_console.print(f"[red]Channel '{channel_ref}' not found in cache.[/red]")
    error_console.print("[dim]Run 'slack convos list --refresh' to update the cache.[/dim]")
    raise typer.Exit(1)


def format_message_text(text: str, indent: str = "  ") -> str:
    """Format message text with indentation.

    Args:
        text: The message text.
        indent: Indentation string.

    Returns:
        Formatted text.
    """
    if not text:
        return f"{indent}(no text)"
    lines = text.split("\n")
    return "\n".join(f"{indent}{line}" for line in lines)


def format_reactions(
    reactions: list[dict[str, Any]] | None,
    mode: str,
    users: dict[str, str],
) -> str:
    """Format reactions for display.

    Args:
        reactions: List of reaction data from Slack API.
        mode: 'off', 'counts', or 'names'.
        users: Dictionary mapping user ID to display name.

    Returns:
        Formatted reactions string.
    """
    if mode == "off" or not reactions:
        return ""

    parts = []
    for reaction in reactions:
        emoji = reaction.get("name", "")
        count = reaction.get("count", 0)
        user_ids = reaction.get("users", [])

        if mode == "counts":
            parts.append(f":{emoji}: {count}")
        elif mode == "names":
            names = [users.get(uid, uid) for uid in user_ids]
            parts.append(f":{emoji}: {', '.join(names)}")

    return " ".join(parts)


def fetch_channel_messages(
    client: WebClient,
    channel_id: str,
    oldest: datetime | None,
    latest: datetime | None,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch messages from a channel.

    Args:
        client: The Slack WebClient.
        channel_id: The channel ID.
        oldest: Oldest message time (inclusive).
        latest: Latest message time (inclusive).
        limit: Maximum number of messages to fetch.

    Returns:
        List of message data from API.
    """
    messages: list[dict[str, Any]] = []
    cursor: str | None = None

    kwargs: dict[str, Any] = {
        "channel": channel_id,
        "limit": min(limit, 1000),  # API max is 1000
    }

    if oldest:
        kwargs["oldest"] = datetime_to_slack_ts(oldest)
    if latest:
        kwargs["latest"] = datetime_to_slack_ts(latest)

    while len(messages) < limit:
        if cursor:
            kwargs["cursor"] = cursor

        try:
            logger.debug(f"Fetching messages (cursor: {cursor or 'initial'})")
            response = client.conversations_history(**kwargs)

            if not response["ok"]:
                raise SlackApiError(f"API error: {response.get('error', 'unknown')}", response)

            batch = response.get("messages", [])
            messages.extend(batch)

            # Check for more pages
            if not response.get("has_more", False):
                break

            response_metadata = response.get("response_metadata", {})
            cursor = response_metadata.get("next_cursor")
            if not cursor:
                break

            # Adjust limit for next request
            remaining = limit - len(messages)
            kwargs["limit"] = min(remaining, 1000)

        except SlackApiError as e:
            error_console.print(f"[red]Slack API error: {e.response.get('error', str(e))}[/red]")
            raise typer.Exit(1) from None

    # Trim to exact limit
    return messages[:limit]


def fetch_thread_replies(
    client: WebClient,
    channel_id: str,
    thread_ts: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch replies in a thread.

    Args:
        client: The Slack WebClient.
        channel_id: The channel ID.
        thread_ts: The thread timestamp.
        limit: Maximum number of messages to fetch.

    Returns:
        List of message data from API (parent first, then replies).
    """
    messages: list[dict[str, Any]] = []
    cursor: str | None = None

    while len(messages) < limit:
        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "ts": thread_ts,
            "limit": min(limit - len(messages), 1000),
        }
        if cursor:
            kwargs["cursor"] = cursor

        try:
            logger.debug(f"Fetching thread replies (cursor: {cursor or 'initial'})")
            response = client.conversations_replies(**kwargs)

            if not response["ok"]:
                raise SlackApiError(f"API error: {response.get('error', 'unknown')}", response)

            batch = response.get("messages", [])
            messages.extend(batch)

            # Check for more pages
            if not response.get("has_more", False):
                break

            response_metadata = response.get("response_metadata", {})
            cursor = response_metadata.get("next_cursor")
            if not cursor:
                break

        except SlackApiError as e:
            error_console.print(f"[red]Slack API error: {e.response.get('error', str(e))}[/red]")
            raise typer.Exit(1) from None

    return messages[:limit]


def display_channel_messages(
    messages: list[dict[str, Any]],
    users: dict[str, str],
    reactions_mode: str,
) -> None:
    """Display channel messages.

    Args:
        messages: List of messages from API.
        users: Dictionary mapping user ID to display name.
        reactions_mode: How to display reactions.
    """
    # Messages come in reverse chronological order, reverse for display
    for msg in reversed(messages):
        ts = msg.get("ts", "")
        user_id = msg.get("user", "")
        text = msg.get("text", "")
        reply_count = msg.get("reply_count", 0)
        reactions = msg.get("reactions", [])

        # Format timestamp
        try:
            dt = slack_ts_to_datetime(ts)
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            time_str = ts

        # Get user display name
        user_name = users.get(user_id, user_id) if user_id else "(unknown)"
        if user_name and not user_name.startswith("@"):
            user_name = f"@{user_name}"

        # Build the header line
        print(f"{time_str}  {user_name}")

        # Print message text
        print(format_message_text(text))

        # Print metadata line (replies, reactions)
        meta_parts = []
        if reply_count > 0:
            meta_parts.append(f"[{reply_count} replies, thread_ts={ts}]")

        reactions_str = format_reactions(reactions, reactions_mode, users)
        if reactions_str:
            meta_parts.append(reactions_str)

        if meta_parts:
            print(f"  {' '.join(meta_parts)}")

        print()  # Blank line between messages


def display_thread_messages(
    messages: list[dict[str, Any]],
    users: dict[str, str],
    reactions_mode: str,
) -> None:
    """Display thread messages with parent and replies.

    Args:
        messages: List of messages from API (parent first).
        users: Dictionary mapping user ID to display name.
        reactions_mode: How to display reactions.
    """
    if not messages:
        return

    # First message is the parent
    parent = messages[0]
    replies = messages[1:]

    # Display parent
    ts = parent.get("ts", "")
    user_id = parent.get("user", "")
    text = parent.get("text", "")
    reactions = parent.get("reactions", [])

    try:
        dt = slack_ts_to_datetime(ts)
        time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, OSError):
        time_str = ts

    user_name = users.get(user_id, user_id) if user_id else "(unknown)"
    if user_name and not user_name.startswith("@"):
        user_name = f"@{user_name}"

    print(f"{time_str}  {user_name} [parent]")
    print(format_message_text(text))

    reactions_str = format_reactions(reactions, reactions_mode, users)
    if reactions_str:
        print(f"  {reactions_str}")

    print()  # Blank line after parent

    # Display replies (indented)
    for reply in replies:
        ts = reply.get("ts", "")
        user_id = reply.get("user", "")
        text = reply.get("text", "")
        reactions = reply.get("reactions", [])

        try:
            dt = slack_ts_to_datetime(ts)
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            time_str = ts

        user_name = users.get(user_id, user_id) if user_id else "(unknown)"
        if user_name and not user_name.startswith("@"):
            user_name = f"@{user_name}"

        print(f"  {time_str}  {user_name}")
        print(format_message_text(text, indent="    "))

        reactions_str = format_reactions(reactions, reactions_mode, users)
        if reactions_str:
            print(f"    {reactions_str}")

        print()  # Blank line between replies


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
    org = cli_ctx.get_org()
    org_name = org.name

    # Resolve channel
    channel_id = resolve_channel(org_name, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # Create Slack client
    client = WebClient(token=org.token)

    # Fetch messages
    if thread_ts:
        console.print(f"[dim]Fetching thread replies for {thread_ts}...[/dim]")
        fetched_messages = fetch_thread_replies(client, channel_id, thread_ts, limit)
    else:
        time_range = ""
        if oldest:
            time_range = f" from {oldest.strftime('%Y-%m-%d %H:%M')}"
        if latest:
            time_range += f" to {latest.strftime('%Y-%m-%d %H:%M')}"
        console.print(f"[dim]Fetching messages{time_range}...[/dim]")
        fetched_messages = fetch_channel_messages(client, channel_id, oldest, latest, limit)

    if not fetched_messages:
        console.print("[yellow]No messages found.[/yellow]")
        return

    console.print(f"[dim]Found {len(fetched_messages)} messages[/dim]\n")

    # Collect user IDs for resolution
    user_ids: set[str] = set()
    for msg in fetched_messages:
        if user_id := msg.get("user"):
            user_ids.add(user_id)
        # Also collect user IDs from reactions if showing names
        if reactions == "names":
            for reaction in msg.get("reactions", []):
                user_ids.update(reaction.get("users", []))

    # Resolve user names
    users = get_user_display_names(client, org_name, list(user_ids))

    # Display messages
    if thread_ts:
        display_thread_messages(fetched_messages, users, reactions)
    else:
        display_channel_messages(fetched_messages, users, reactions)
