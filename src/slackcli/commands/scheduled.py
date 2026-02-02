"""Scheduled messages command group for Slack CLI."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any

import typer
from slack_sdk.errors import SlackApiError

from ..context import get_context
from ..errors import format_error_with_hint
from ..logging import console, error_console, get_logger
from ..output import format_message_text, output_json
from ..time_utils import parse_future_time
from .messages import resolve_channel

logger = get_logger(__name__)

app = typer.Typer(
    name="scheduled",
    help="Manage scheduled messages.",
    no_args_is_help=True,
    rich_markup_mode=None,
)


def _format_scheduled_message(
    msg: dict[str, Any],
    users: dict[str, str],
    channels: dict[str, str],
) -> dict[str, Any]:
    """Format a scheduled message for output.

    Args:
        msg: The raw scheduled message from API.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Formatted scheduled message dictionary.
    """
    channel_id = msg.get("channel_id", "")
    post_at = msg.get("post_at", 0)
    scheduled_message_id = msg.get("id", "")
    text = msg.get("text", "")

    # Format timestamp to datetime (display in local timezone)
    if post_at:
        try:
            dt = datetime.fromtimestamp(post_at).astimezone()
            datetime_str = dt.strftime("%Y-%m-%d %H:%M:%S %Z")
        except (ValueError, OSError):
            datetime_str = str(post_at)
    else:
        datetime_str = ""

    return {
        "scheduled_message_id": scheduled_message_id,
        "channel_id": channel_id,
        "channel_name": channels.get(channel_id, channel_id),
        "post_at": post_at,
        "post_at_datetime": datetime_str,
        "text": text,
    }


@app.command("list")
def list_scheduled(
    channel: Annotated[
        str | None,
        typer.Argument(
            help="Channel reference (#channel-name or channel ID) to filter by.",
        ),
    ] = None,
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output the result as JSON.",
        ),
    ] = False,
) -> None:
    """List scheduled messages.

    Examples:
        slack scheduled list
        slack scheduled list '#general'
        slack scheduled list --json
    """
    # Get org context
    ctx = get_context()
    slack = ctx.get_slack_client()

    # Resolve channel if provided
    channel_id: str | None = None
    channel_name: str | None = None
    if channel:
        channel_id, channel_name = resolve_channel(slack, channel)
        logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # List scheduled messages
    try:
        if not output_json_flag:
            if channel_name:
                console.print(f"[dim]Fetching scheduled messages for #{channel_name}...[/dim]")
            else:
                console.print("[dim]Fetching scheduled messages...[/dim]")

        result = slack.list_scheduled_messages(channel_id=channel_id)
        messages = result.get("scheduled_messages", [])

        if not messages:
            if output_json_flag:
                output_json({"scheduled_messages": []})
            else:
                console.print("[yellow]No scheduled messages found.[/yellow]")
            return

        # Get channel names from cache
        channels = slack.get_channel_names()

        # Collect user IDs - scheduled messages don't typically have user IDs in list response
        users: dict[str, str] = {}

        # Format messages
        formatted_messages = [_format_scheduled_message(msg, users, channels) for msg in messages]

        if output_json_flag:
            output_json({"scheduled_messages": formatted_messages})
        else:
            console.print(f"[dim]Found {len(formatted_messages)} scheduled messages[/dim]\n")
            for msg in formatted_messages:
                channel_display = f"#{msg['channel_name']}" if msg["channel_name"] else msg["channel_id"]
                print(f"{msg['post_at_datetime']}  {channel_display}")

                # Truncate text for preview (first 200 chars)
                text = msg["text"]
                if len(text) > 200:
                    text = text[:200] + "..."
                print(format_message_text(text))

                console.print(f"  [dim]id={msg['scheduled_message_id']}[/dim]")
                print()  # Blank line between messages

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None


@app.command("create")
def create_scheduled(
    channel: Annotated[
        str,
        typer.Argument(
            help="Channel reference (#channel-name or channel ID).",
        ),
    ],
    post_at: Annotated[
        str,
        typer.Argument(
            help="When to send: ISO datetime, 'in 1h', 'in 30m', 'tomorrow', 'tomorrow 9am'.",
        ),
    ],
    message: Annotated[
        str,
        typer.Argument(
            help="Message text to schedule.",
        ),
    ],
    thread: Annotated[
        str | None,
        typer.Option(
            "--thread",
            "-t",
            help="Thread timestamp to reply to.",
        ),
    ] = None,
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output the result as JSON.",
        ),
    ] = False,
) -> None:
    """Schedule a message for future delivery.

    Examples:
        slack scheduled create '#general' "2025-02-03 09:00" "Good morning team!"
        slack scheduled create '#general' "in 1h" "Reminder!"
        slack scheduled create '#general' "in 30m" "Meeting starting soon"
        slack scheduled create '#general' "tomorrow" "Daily standup"
        slack scheduled create '#general' "tomorrow 9am" "Good morning!"
        slack scheduled create '#general' --thread 1234567890.123456 "in 1h" "Thread reply"
    """
    # Parse the time specification
    try:
        scheduled_time = parse_future_time(post_at)
    except ValueError as e:
        error_console.print(f"[red]{e}[/red]")
        raise typer.Exit(1) from None

    # Check if time is in the future (use local time for comparison)
    now = datetime.now().astimezone()
    if scheduled_time <= now:
        error_console.print(f"[red]Scheduled time must be in the future. Got: {scheduled_time.isoformat()}[/red]")
        raise typer.Exit(1)

    # Check 120 day limit
    max_future = now + timedelta(days=120)
    if scheduled_time > max_future:
        error_console.print("[red]Scheduled time cannot be more than 120 days in the future.[/red]")
        raise typer.Exit(1)

    # Get org context
    ctx = get_context()
    slack = ctx.get_slack_client()

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # Schedule message
    try:
        if not output_json_flag:
            time_str = scheduled_time.strftime("%Y-%m-%d %H:%M:%S %Z")
            if thread:
                console.print(f"[dim]Scheduling reply in thread {thread} in #{channel_name} for {time_str}...[/dim]")
            else:
                console.print(f"[dim]Scheduling message in #{channel_name} for {time_str}...[/dim]")

        result = slack.schedule_message(
            channel_id,
            message,
            post_at=int(scheduled_time.timestamp()),
            thread_ts=thread,
        )

        if output_json_flag:
            output_json(result)
        else:
            scheduled_message_id = result.get("scheduled_message_id", "unknown")
            post_at_ts = result.get("post_at", scheduled_time.timestamp())
            # Display in local timezone
            post_at_dt = datetime.fromtimestamp(post_at_ts).astimezone()
            post_at_str = post_at_dt.strftime("%Y-%m-%d %H:%M:%S %Z")
            console.print("[green]Message scheduled successfully.[/green]")
            console.print(f"[dim]scheduled_message_id={scheduled_message_id}, post_at={post_at_str}[/dim]")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None


@app.command("delete")
def delete_scheduled(
    channel: Annotated[
        str,
        typer.Argument(
            help="Channel reference (#channel-name or channel ID).",
        ),
    ],
    scheduled_message_id: Annotated[
        str,
        typer.Argument(
            help="The scheduled message ID to delete.",
        ),
    ],
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output the result as JSON.",
        ),
    ] = False,
) -> None:
    """Delete a scheduled message.

    Examples:
        slack scheduled delete '#general' Q1234ABCD5678EFGH
        slack scheduled delete C0123456789 Q1234ABCD5678EFGH
    """
    # Get org context
    ctx = get_context()
    slack = ctx.get_slack_client()

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # Delete scheduled message
    try:
        if not output_json_flag:
            console.print(f"[dim]Deleting scheduled message {scheduled_message_id} from #{channel_name}...[/dim]")

        result = slack.delete_scheduled_message(channel_id, scheduled_message_id)

        if output_json_flag:
            output_json(result)
        else:
            console.print("[green]Scheduled message deleted successfully.[/green]")
            console.print(f"[dim]id={scheduled_message_id}[/dim]")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None
