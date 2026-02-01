"""Messages command for Slack CLI."""

from __future__ import annotations

import json as json_module
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.console import Console
from slack_sdk.errors import SlackApiError

from ..blocks import get_message_text
from ..context import get_context
from ..logging import error_console, get_logger
from .conversations import load_conversations_from_cache

if TYPE_CHECKING:
    pass

console = Console()
logger = get_logger(__name__)


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


def resolve_channel(org_name: str, channel_ref: str) -> tuple[str, str]:
    """Resolve a channel reference to a channel ID and name.

    Args:
        org_name: The organization name for cache lookup.
        channel_ref: Channel reference - either '#channel-name' or raw ID.

    Returns:
        Tuple of (channel_id, channel_name).

    Raises:
        typer.Exit: If channel cannot be resolved.
    """
    # Load conversations from cache (no API call, just read cache)
    conversations = load_conversations_from_cache(org_name)
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


def resolve_slack_mentions(text: str, users: dict[str, str], channels: dict[str, str]) -> str:
    """Replace Slack mention macros with readable names.

    Handles:
    - <@U08GTCPJW95> - user mentions, replaced with @username
    - <#C01234567> or <#C01234567|channel-name> - channel mentions, replaced with #channel-name
    - <https://example.com|link text> - links, replaced with the URL
    - <!subteam^S123|@team-name> - user group mentions, replaced with @team-name

    Args:
        text: The original message text with Slack formatting.
        users: Dictionary mapping user ID to username.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Text with mentions replaced with readable names.
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
        # If the mention includes a name, use it
        channel_name_in_mention = match.group(2)
        if channel_name_in_mention:
            return f"#{channel_name_in_mention}"
        # Otherwise look up from cache
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

    # Replace user group mentions: <!subteam^S123|@team-name> or <!subteam^S123>
    def replace_subteam(match: re.Match) -> str:
        team_name = match.group(2)
        if team_name:
            return team_name
        return f"@subteam-{match.group(1)}"

    text = re.sub(r"<!subteam\^([A-Z0-9]+)(?:\|([^>]*))?>", replace_subteam, text)

    # Replace special mentions: <!here>, <!channel>, <!everyone>
    text = re.sub(r"<!here>", "@here", text)
    text = re.sub(r"<!channel>", "@channel", text)
    text = re.sub(r"<!everyone>", "@everyone", text)

    return text


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


def display_channel_messages(
    messages: list[dict[str, Any]],
    users: dict[str, str],
    channels: dict[str, str],
    reactions_mode: str,
    with_threads: bool = False,
) -> None:
    """Display channel messages.

    Args:
        messages: List of messages from API.
        users: Dictionary mapping user ID to username.
        channels: Dictionary mapping channel ID to channel name.
        reactions_mode: How to display reactions.
        with_threads: Whether to display inline thread replies.
    """
    # Messages come in reverse chronological order, reverse for display
    for msg in reversed(messages):
        ts = msg.get("ts", "")
        user_id = msg.get("user", "")
        reply_count = msg.get("reply_count", 0)
        reactions = msg.get("reactions", [])
        replies = msg.get("replies", [])

        # Get message text, falling back to blocks/attachments if needed
        text = get_message_text(msg, users, channels)

        # Resolve any remaining mentions in text
        text = resolve_slack_mentions(text, users, channels)

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
            if with_threads and replies:
                meta_parts.append(f"[{reply_count} replies]")
            else:
                meta_parts.append(f"[{reply_count} replies, thread_ts={ts}]")

        reactions_str = format_reactions(reactions, reactions_mode, users)
        if reactions_str:
            meta_parts.append(reactions_str)

        if meta_parts:
            print(f"  {' '.join(meta_parts)}")

        # Display inline thread replies if present
        if with_threads and replies:
            print()  # Blank line before replies
            for reply in replies:
                reply_ts = reply.get("ts", "")
                reply_user_id = reply.get("user", "")
                reply_reactions = reply.get("reactions", [])

                # Get reply text, falling back to blocks/attachments if needed
                reply_text = get_message_text(reply, users, channels)

                # Resolve any remaining mentions in reply text
                reply_text = resolve_slack_mentions(reply_text, users, channels)

                # Format timestamp
                try:
                    reply_dt = slack_ts_to_datetime(reply_ts)
                    reply_time_str = reply_dt.strftime("%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError):
                    reply_time_str = reply_ts

                # Get user display name
                reply_user_name = users.get(reply_user_id, reply_user_id) if reply_user_id else "(unknown)"
                if reply_user_name and not reply_user_name.startswith("@"):
                    reply_user_name = f"@{reply_user_name}"

                # Print indented reply
                print(f"    {reply_time_str}  {reply_user_name}")
                print(format_message_text(reply_text, indent="      "))

                # Print reactions for reply
                reply_reactions_str = format_reactions(reply_reactions, reactions_mode, users)
                if reply_reactions_str:
                    print(f"      {reply_reactions_str}")

        print()  # Blank line between messages


def display_thread_messages(
    messages: list[dict[str, Any]],
    users: dict[str, str],
    channels: dict[str, str],
    reactions_mode: str,
) -> None:
    """Display thread messages with parent and replies.

    Args:
        messages: List of messages from API (parent first).
        users: Dictionary mapping user ID to username.
        channels: Dictionary mapping channel ID to channel name.
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
    reactions = parent.get("reactions", [])

    # Get message text, falling back to blocks/attachments if needed
    text = get_message_text(parent, users, channels)

    # Resolve any remaining mentions in text
    text = resolve_slack_mentions(text, users, channels)

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
        reactions = reply.get("reactions", [])

        # Get reply text, falling back to blocks/attachments if needed
        text = get_message_text(reply, users, channels)

        # Resolve any remaining mentions in text
        text = resolve_slack_mentions(text, users, channels)

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


def output_json_messages(
    messages: list[dict[str, Any]],
    users: dict[str, str],
    channels: dict[str, str],
    channel_id: str,
    channel_name: str,
    with_threads: bool = False,
) -> None:
    """Output messages as JSON.

    Uses plain print() to avoid Rich console formatting.

    Args:
        messages: List of messages from API.
        users: Dictionary mapping user ID to username.
        channels: Dictionary mapping channel ID to channel name.
        channel_id: The channel ID.
        channel_name: The channel name.
        with_threads: Whether to include thread replies.
    """
    output_messages = []

    # Messages come in reverse chronological order, reverse for output
    for msg in reversed(messages):
        ts = msg.get("ts", "")
        user_id = msg.get("user", "")
        thread_ts = msg.get("thread_ts")
        reply_count = msg.get("reply_count", 0)
        reactions_data = msg.get("reactions", [])
        replies_data = msg.get("replies", [])

        # Get message text, falling back to blocks/attachments if needed
        text = get_message_text(msg, users, channels)

        # Resolve any remaining mentions in text
        resolved_text = resolve_slack_mentions(text, users, channels)

        # Get username
        user_name = users.get(user_id, user_id) if user_id else None

        # Format reactions with usernames
        formatted_reactions = []
        for reaction in reactions_data:
            reaction_users = [users.get(uid, uid) for uid in reaction.get("users", [])]
            formatted_reactions.append(
                {
                    "name": reaction.get("name", ""),
                    "count": reaction.get("count", 0),
                    "users": reaction_users,
                }
            )

        message_obj = {
            "ts": ts,
            "user_id": user_id,
            "user_name": user_name,
            "text": resolved_text,
            "thread_ts": thread_ts if thread_ts != ts else None,
            "reply_count": reply_count,
            "reactions": formatted_reactions,
        }

        # Add replies if with_threads is enabled and there are replies
        if with_threads and replies_data:
            formatted_replies = []
            for reply in replies_data:
                reply_ts = reply.get("ts", "")
                reply_user_id = reply.get("user", "")
                reply_reactions_data = reply.get("reactions", [])

                # Get reply text, falling back to blocks/attachments if needed
                reply_text = get_message_text(reply, users, channels)

                # Resolve any remaining mentions in reply text
                resolved_reply_text = resolve_slack_mentions(reply_text, users, channels)

                # Get username
                reply_user_name = users.get(reply_user_id, reply_user_id) if reply_user_id else None

                # Format reactions with usernames
                formatted_reply_reactions = []
                for reaction in reply_reactions_data:
                    reaction_users = [users.get(uid, uid) for uid in reaction.get("users", [])]
                    formatted_reply_reactions.append(
                        {
                            "name": reaction.get("name", ""),
                            "count": reaction.get("count", 0),
                            "users": reaction_users,
                        }
                    )

                formatted_replies.append(
                    {
                        "ts": reply_ts,
                        "user_id": reply_user_id,
                        "user_name": reply_user_name,
                        "text": resolved_reply_text,
                        "reactions": formatted_reply_reactions,
                    }
                )
            message_obj["replies"] = formatted_replies

        output_messages.append(message_obj)

    output = {
        "channel": channel_id,
        "channel_name": channel_name,
        "messages": output_messages,
    }

    # Use plain print() to avoid Rich formatting
    print(json_module.dumps(output, indent=2, ensure_ascii=False))


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
    channel_id, channel_name = resolve_channel(slack.org_name, channel)
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
            print(
                json_module.dumps(
                    {"channel": channel_id, "channel_name": channel_name, "messages": []}, indent=2, ensure_ascii=False
                )
            )
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
    user_ids: set[str] = set()
    for msg in fetched_messages:
        if user_id := msg.get("user"):
            user_ids.add(user_id)
        # Also collect user IDs from reactions if showing names
        if reactions == "names" or output_json:
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
                # Collect user IDs from reactions in replies
                if reactions == "names" or output_json:
                    for reaction in reply.get("reactions", []):
                        user_ids.update(reaction.get("users", []))
                # Collect user IDs from mentions in reply text
                reply_text = reply.get("text", "")
                if reply_text:
                    mentioned_users = re.findall(r"<@([A-Z0-9]+)(?:\|[^>]*)?>", reply_text)
                    user_ids.update(mentioned_users)

    # Resolve user names
    users = slack.get_user_display_names(list(user_ids))

    # Get channel names from cache for mention resolution
    channels = slack.get_channel_names()

    # Output messages
    if output_json:
        output_json_messages(fetched_messages, users, channels, channel_id, channel_name, with_threads)
    elif thread_ts:
        display_thread_messages(fetched_messages, users, channels, reactions)
    else:
        display_channel_messages(fetched_messages, users, channels, reactions, with_threads)
