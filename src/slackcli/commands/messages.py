"""Messages command group for Slack CLI."""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer
from slack_sdk.errors import SlackApiError

from ..blocks import get_message_text
from ..context import get_context
from ..errors import format_error_with_hint
from ..logging import console, error_console, get_logger
from ..models import Message, MessagesOutput, resolve_slack_mentions
from ..output import (
    output_json,
    output_messages_json,
    output_messages_text,
    output_thread_text,
)
from ..time_utils import parse_time_spec

if TYPE_CHECKING:
    from ..client import SlackCli

logger = get_logger(__name__)

app = typer.Typer(
    name="messages",
    help="Manage Slack messages.",
    no_args_is_help=True,
    rich_markup_mode=None,
)


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


def resolve_target(slack: SlackCli, target: str) -> tuple[str, str, bool]:
    """Resolve a target (channel or user) to a channel ID and name.

    Args:
        slack: The SlackCli client.
        target: Target reference - '#channel', 'C...' for channel, '@user' or 'U...' for user DM.

    Returns:
        Tuple of (channel_id, display_name, is_dm).

    Raises:
        typer.Exit: If target cannot be resolved.
    """
    # Check if this is a user reference (DM)
    if target.startswith("@") or (target.startswith("U") and re.match(r"^U[A-Z0-9]+$", target)):
        # It's a user reference - resolve to DM
        resolved = slack.resolve_user(target)
        if resolved is None:
            error_console.print(f"[red]Could not resolve user '{target}'.[/red]")
            error_console.print("[dim]Hint: Try @username, @email@example.com, or a raw user ID (U...).[/dim]")
            raise typer.Exit(1)

        user_id, username = resolved
        logger.debug(f"Resolved user '{target}' to '{user_id}' (@{username})")

        # Open DM conversation
        try:
            dm_channel = slack.open_dm(user_id)
            dm_channel_id = dm_channel.get("channel", {}).get("id")

            if not dm_channel_id:
                error_console.print("[red]Failed to open DM channel.[/red]")
                raise typer.Exit(1)

            logger.debug(f"Opened DM channel {dm_channel_id} with user {user_id}")
            return dm_channel_id, f"@{username}", True

        except SlackApiError as e:
            error_msg, hint = format_error_with_hint(e)
            error_console.print(f"[red]Failed to open DM: {error_msg}[/red]")
            if hint:
                error_console.print(f"[dim]Hint: {hint}[/dim]")
            raise typer.Exit(1) from None

    # It's a channel reference
    channel_id, channel_name = resolve_channel(slack, target)
    return channel_id, f"#{channel_name}", False


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
    # Client now returns messages in ascending order.
    messages = [
        Message.from_api(msg, users, channels, get_message_text, resolve_slack_mentions) for msg in raw_messages
    ]

    return MessagesOutput(
        channel_id=channel_id,
        channel_name=channel_name,
        messages=messages,
    )


@app.command("list")
def list_messages(
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
    head: Annotated[
        int | None,
        typer.Option(
            "--head",
            help="Return the first N messages in the window (walk forward).",
        ),
    ] = None,
    tail: Annotated[
        int | None,
        typer.Option(
            "--tail",
            help="Return the last N messages in the window (walk backward). Default: 25.",
        ),
    ] = None,
    after: Annotated[
        str | None,
        typer.Option(
            "--after",
            help="Return up to 25 messages strictly after this ts (forward cursor).",
        ),
    ] = None,
    before: Annotated[
        str | None,
        typer.Option(
            "--before",
            help="Return up to 25 messages strictly before this ts (backward cursor).",
        ),
    ] = None,
    reactions: Annotated[
        str,
        typer.Option(
            "--reactions",
            help="How to show reactions: 'off', 'counts', or 'names'.",
        ),
    ] = "off",
    output_json_flag: Annotated[
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

    Exactly one of --head / --tail / --after / --before may be set. If none
    are specified, --tail 25 is used. Display order is always ascending
    (oldest on top, newest on bottom) regardless of the direction flag.

    Examples:
        slack messages list '#general'                        # last 25
        slack messages list '#general' --tail 5
        slack messages list '#general' --after 1234567890.123456
        slack messages list '#general' --before 1234567890.123456
        slack messages list '#general' --head 100 --since 2024-01-01
        slack messages list '#general' 1234567890.123456      # thread replies
    """
    # Validate reactions option
    if reactions not in ("off", "counts", "names"):
        error_console.print(f"[red]Invalid --reactions value: {reactions}. Use 'off', 'counts', or 'names'.[/red]")
        raise typer.Exit(1)

    # Validate mutually exclusive direction flags.
    direction_flags = {
        "--head": head is not None,
        "--tail": tail is not None,
        "--after": after is not None,
        "--before": before is not None,
    }
    set_flags = [name for name, is_set in direction_flags.items() if is_set]
    if len(set_flags) > 1:
        error_console.print(
            f"[red]--head / --tail / --after / --before are mutually exclusive. Got: {', '.join(set_flags)}[/red]"
        )
        raise typer.Exit(1)

    # Resolve (direction, count, after_ts, before_ts).
    DEFAULT_CURSOR_PAGE = 25
    direction: str
    count: int
    after_ts: str | None = None
    before_ts: str | None = None
    if head is not None:
        direction, count = "head", head
    elif tail is not None:
        direction, count = "tail", tail
    elif after is not None:
        direction, count = "head", DEFAULT_CURSOR_PAGE
        after_ts = after
    elif before is not None:
        direction, count = "tail", DEFAULT_CURSOR_PAGE
        before_ts = before
    else:
        direction, count = "tail", DEFAULT_CURSOR_PAGE

    if count <= 0:
        error_console.print("[red]Count must be a positive integer.[/red]")
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
    ctx = get_context()
    slack = ctx.get_slack_client()

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # Fetch messages
    has_more_before = False
    has_more_after = False
    thread_total_count = 0
    thread_parent_raw: dict[str, Any] | None = None
    try:
        if thread_ts:
            if not output_json_flag:
                console.print(f"[dim]Fetching thread replies for {thread_ts}...[/dim]")
            # Thread mode: parent counts toward the requested count. Ask the
            # client for count-1 replies in head mode so parent + replies
            # total to count. In tail mode, fetch count replies — parent
            # itself may be replaced by a placeholder if the thread
            # overflows.
            reply_count_request = count - 1 if direction == "head" else count
            if reply_count_request < 1:
                reply_count_request = 1
            fetched_messages, has_more_before, has_more_after = slack.get_thread_replies(
                channel_id,
                thread_ts,
                direction=direction,  # type: ignore[arg-type]
                count=reply_count_request,
                after_ts=after_ts,
                before_ts=before_ts,
            )
            if fetched_messages:
                thread_parent_raw = fetched_messages[0]
                thread_total_count = 1 + (thread_parent_raw.get("reply_count", 0) or 0)
        else:
            time_range = ""
            if oldest:
                time_range = f" from {oldest.strftime('%Y-%m-%d %H:%M')}"
            if latest:
                time_range += f" to {latest.strftime('%Y-%m-%d %H:%M')}"
            if not output_json_flag:
                console.print(f"[dim]Fetching messages{time_range}...[/dim]")
            fetched_messages, has_more_before, has_more_after = slack.get_messages(
                channel_id,
                direction=direction,  # type: ignore[arg-type]
                count=count,
                oldest=oldest,
                latest=latest,
                after_ts=after_ts,
                before_ts=before_ts,
            )
    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")
        raise typer.Exit(1) from None

    # Thread placeholder logic: in --tail mode with overflow, drop the
    # parent content and present a placeholder instead. ``thread_total_count``
    # accounts for parent + replies from Slack's reply_count field.
    thread_parent_omitted = False
    if thread_ts and fetched_messages and direction == "tail" and thread_total_count > count:
        # Drop parent from the rendered slice; only replies are shown plus a
        # placeholder line printed by the renderer.
        fetched_messages = fetched_messages[1:]
        thread_parent_omitted = True
        # Older replies or the parent itself are above the window.
        has_more_before = True

    if not fetched_messages and not thread_parent_omitted:
        if output_json_flag:
            output = MessagesOutput(
                channel_id=channel_id,
                channel_name=channel_name,
                messages=[],
                has_more_before=has_more_before,
                has_more_after=has_more_after,
            )
            output_messages_json(output, with_threads)
        else:
            console.print("[yellow]No messages found.[/yellow]")
        return

    if not output_json_flag:
        shown = len(fetched_messages) + (1 if thread_parent_omitted else 0)
        console.print(f"[dim]Found {shown} messages[/dim]\n")

    # Fetch thread replies if --with-threads is enabled and not already viewing a thread
    if with_threads and thread_ts is None:
        messages_with_threads = [msg for msg in fetched_messages if msg.get("reply_count", 0) > 0]
        if messages_with_threads and not output_json_flag:
            console.print(f"[dim]Fetching {len(messages_with_threads)} threads...[/dim]")

        for msg in fetched_messages:
            reply_count = msg.get("reply_count", 0)
            if reply_count > 0:
                msg_ts = msg.get("ts", "")
                try:
                    thread_messages = slack.fetch_full_thread(channel_id, msg_ts)
                    if thread_messages:
                        # Skip the parent to avoid duplication.
                        msg["replies"] = [m for m in thread_messages if m.get("ts") != msg_ts]
                except SlackApiError as e:
                    logger.debug(f"Failed to fetch thread {msg_ts}: {e}")

    # Collect user IDs for resolution
    include_reaction_users = reactions == "names" or output_json_flag
    collect_source = list(fetched_messages)
    if thread_parent_omitted and thread_parent_raw is not None:
        collect_source = [thread_parent_raw, *collect_source]
    user_ids = collect_user_ids_from_messages(
        collect_source,
        include_reaction_users=include_reaction_users,
        with_threads=with_threads,
    )

    # Resolve user names
    users = slack.get_user_display_names(list(user_ids))

    # Get channel names from cache for mention resolution
    channels = slack.get_channel_names()

    # Convert to model
    messages_output = convert_messages_to_model(fetched_messages, users, channels, channel_id, channel_name)

    # Populate pagination envelope.
    messages_output.has_more_before = has_more_before
    messages_output.has_more_after = has_more_after
    rendered = messages_output.messages
    if rendered:
        messages_output.next_before_ts = rendered[0].ts
        messages_output.next_after_ts = rendered[-1].ts

    if thread_parent_omitted and thread_parent_raw is not None:
        omitted_parent_msg = Message.from_api(
            thread_parent_raw, users, channels, get_message_text, resolve_slack_mentions
        )
        messages_output.thread_parent_omitted = True
        messages_output.omitted_parent = omitted_parent_msg

    # Output messages
    if output_json_flag:
        output_messages_json(messages_output, with_threads)
    elif thread_ts:
        output_thread_text(messages_output, reactions)
    else:
        output_messages_text(messages_output, reactions, with_threads)


@app.command("send")
def send_message(
    target: Annotated[
        str,
        typer.Argument(
            help="Target: #channel, @user, channel ID (C...), or user ID (U...).",
        ),
    ],
    message: Annotated[
        str | None,
        typer.Argument(
            help="Message text to send. Use --stdin to read from stdin instead.",
        ),
    ] = None,
    thread: Annotated[
        str | None,
        typer.Option(
            "--thread",
            "-t",
            help="Thread timestamp to reply to.",
        ),
    ] = None,
    stdin: Annotated[
        bool,
        typer.Option(
            "--stdin",
            help="Read message text from stdin.",
        ),
    ] = False,
    files: Annotated[
        list[Path] | None,
        typer.Option(
            "--file",
            "-f",
            help="File to upload with the message. Can be specified multiple times.",
            exists=True,
            readable=True,
            resolve_path=True,
        ),
    ] = None,
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output the posted message details as JSON.",
        ),
    ] = False,
) -> None:
    """Send a message to a channel or user (DM).

    The target can be:
    - A channel: #channel-name or C0123456789
    - A user (DM): @username, @email@example.com, or U0123456789

    Examples:
        slack messages send '#general' "Hello world"
        slack messages send '@john.doe' "Hello via DM"
        slack messages send '#general' --thread 1234567890.123456 "Reply in thread"
        echo "Hello" | slack messages send '#general' --stdin
        slack messages send '#general' --file ./report.pdf
        slack messages send '#general' "Here's the report" --file ./report.pdf
    """
    # Validate message input
    has_files = files and len(files) > 0

    if stdin:
        if message is not None:
            error_console.print("[red]Cannot specify both message argument and --stdin.[/red]")
            raise typer.Exit(1)
        # Read from stdin
        if sys.stdin.isatty():
            error_console.print("[red]--stdin specified but no input provided. Pipe content to stdin.[/red]")
            raise typer.Exit(1)
        message = sys.stdin.read()
        if not message.strip():
            error_console.print("[red]Empty message received from stdin.[/red]")
            raise typer.Exit(1)
    elif message is None and not has_files:
        # Message is required unless we have files
        error_console.print(
            "[red]Message text is required. Provide it as an argument, use --stdin, or attach files with --file.[/red]"
        )
        raise typer.Exit(1)

    # Get org context
    ctx = get_context()
    slack = ctx.get_slack_client()

    # Resolve target (channel or user DM)
    channel_id, display_name, is_dm = resolve_target(slack, target)
    logger.debug(f"Resolved target '{target}' to '{channel_id}' ({display_name}, is_dm={is_dm})")

    try:
        results: dict = {
            "ok": True,
            "channel": channel_id,
            "target": display_name,
            "is_dm": is_dm,
        }

        # Send message first if we have one (and we have files)
        # If no files, just send the message normally
        # If files but no message, the first file gets the "initial_comment" treatment
        if message and has_files:
            # Send message first, then upload files (files will be separate from message)
            if not output_json_flag:
                if thread:
                    console.print(f"[dim]Sending reply to thread {thread} in {display_name}...[/dim]")
                else:
                    console.print(f"[dim]Sending message to {display_name}...[/dim]")

            msg_result = slack.send_message(channel_id, message, thread_ts=thread)
            results["message"] = msg_result

            if not output_json_flag:
                ts = msg_result.get("ts", "unknown")
                console.print("[green]Message sent successfully.[/green]")
                console.print(f"[dim]ts={ts}[/dim]")

        elif message:
            # Message only, no files
            if not output_json_flag:
                if thread:
                    console.print(f"[dim]Sending reply to thread {thread} in {display_name}...[/dim]")
                else:
                    console.print(f"[dim]Sending message to {display_name}...[/dim]")

            msg_result = slack.send_message(channel_id, message, thread_ts=thread)
            results["message"] = msg_result

            if not output_json_flag:
                ts = msg_result.get("ts", "unknown")
                console.print("[green]Message sent successfully.[/green]")
                console.print(f"[dim]ts={ts}[/dim]")

        # Upload files
        if has_files:
            results["files"] = []
            for file_path in files:  # type: ignore[union-attr]
                if not output_json_flag:
                    console.print(f"[dim]Uploading {file_path.name}...[/dim]")

                file_result = slack.upload_file(
                    file_path=str(file_path),
                    channel_id=channel_id,
                    thread_ts=thread,
                )
                results["files"].append(file_result)

                if not output_json_flag:
                    file_info = file_result.get("file", {})
                    file_id = file_info.get("id", "unknown")
                    console.print(f"[green]File uploaded: {file_path.name}[/green]")
                    console.print(f"[dim]file_id={file_id}[/dim]")

        if output_json_flag:
            output_json(results)

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None


@app.command("edit")
def edit_message(
    channel: Annotated[
        str,
        typer.Argument(
            help="Channel reference (#channel-name or channel ID).",
        ),
    ],
    timestamp: Annotated[
        str,
        typer.Argument(
            help="Message timestamp (ts) to edit.",
        ),
    ],
    message: Annotated[
        str,
        typer.Argument(
            help="New message text.",
        ),
    ],
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output the updated message details as JSON.",
        ),
    ] = False,
) -> None:
    """Edit an existing message in a Slack channel.

    Examples:
        slack messages edit '#general' 1234567890.123456 "Updated message"
        slack messages edit C0123456789 1234567890.123456 "Fixed typo"
    """
    # Validate message text
    if not message.strip():
        error_console.print("[red]Message text cannot be empty.[/red]")
        raise typer.Exit(1)

    # Get org context
    ctx = get_context()
    slack = ctx.get_slack_client()

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # Edit message
    try:
        if not output_json_flag:
            console.print(f"[dim]Editing message {timestamp} in #{channel_name}...[/dim]")

        result = slack.edit_message(channel_id, timestamp, message)

        if output_json_flag:
            output_json(result)
        else:
            console.print("[green]Message edited successfully.[/green]")
            console.print(f"[dim]ts={timestamp}[/dim]")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None


@app.command("delete")
def delete_message(
    channel: Annotated[
        str,
        typer.Argument(
            help="Channel reference (#channel-name or channel ID).",
        ),
    ],
    timestamp: Annotated[
        str,
        typer.Argument(
            help="Message timestamp (ts) to delete.",
        ),
    ],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Skip confirmation prompt.",
        ),
    ] = False,
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output the result as JSON.",
        ),
    ] = False,
) -> None:
    """Delete a message from a Slack channel.

    Examples:
        slack messages delete '#general' 1234567890.123456
        slack messages delete C0123456789 1234567890.123456 --force
    """
    # Get org context
    ctx = get_context()
    slack = ctx.get_slack_client()

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # Confirmation prompt (unless --force is passed or --json is used for scripting)
    if not force and not output_json_flag:
        console.print(f"[yellow]About to delete message {timestamp} from #{channel_name}[/yellow]")
        confirm = typer.confirm("Are you sure you want to delete this message?")
        if not confirm:
            console.print("[dim]Deletion cancelled.[/dim]")
            raise typer.Exit(0)

    # Delete message
    try:
        if not output_json_flag:
            console.print(f"[dim]Deleting message {timestamp} from #{channel_name}...[/dim]")

        result = slack.delete_message(channel_id, timestamp)

        if output_json_flag:
            output_json(result)
        else:
            console.print("[green]Message deleted successfully.[/green]")
            console.print(f"[dim]ts={timestamp}[/dim]")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None
