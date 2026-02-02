"""Pins command group for Slack CLI."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Annotated, Any

import typer
from slack_sdk.errors import SlackApiError

from ..context import get_context
from ..errors import format_error_with_hint
from ..logging import console, error_console, get_logger
from ..output import format_message_text, format_user_name, output_json
from .messages import resolve_channel

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

app = typer.Typer(
    name="pins",
    help="Manage pinned messages.",
    no_args_is_help=True,
    rich_markup_mode=None,
)


def _format_pinned_item(
    item: dict[str, Any],
    users: dict[str, str],
    channels: dict[str, str],
) -> dict[str, Any]:
    """Format a pinned item for output.

    Args:
        item: The raw pinned item from API.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Formatted pinned item dictionary.
    """
    # Extract the message from the item
    message = item.get("message", {})
    created_by = item.get("created_by")
    created = item.get("created")

    # Get message details
    msg_ts = message.get("ts", "")
    msg_user_id = message.get("user", "")
    msg_text = message.get("text", "")

    # Format timestamp to datetime
    if msg_ts:
        try:
            ts_float = float(msg_ts)
            dt = datetime.fromtimestamp(ts_float, tz=timezone.utc)
            datetime_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except (ValueError, OSError):
            datetime_str = msg_ts
    else:
        datetime_str = ""

    return {
        "ts": msg_ts,
        "datetime": datetime_str,
        "user_id": msg_user_id,
        "user_name": users.get(msg_user_id, msg_user_id),
        "text": msg_text,
        "pinned_by_id": created_by,
        "pinned_by_name": users.get(created_by, created_by) if created_by else None,
        "pinned_at": created,
    }


@app.command("list")
def list_pins(
    channel: Annotated[
        str,
        typer.Argument(
            help="Channel reference (#channel-name or channel ID).",
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
    """List pinned messages in a channel.

    Examples:
        slack pins list '#general'
        slack pins list '#general' --json
        slack pins list C0123456789
    """
    # Get org context
    ctx = get_context()
    slack = ctx.get_slack_client()

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # List pins
    try:
        if not output_json_flag:
            console.print(f"[dim]Fetching pinned messages in #{channel_name}...[/dim]")

        result = slack.list_pins(channel_id)
        items = result.get("items", [])

        if not items:
            if output_json_flag:
                output_json(
                    {
                        "channel": channel_id,
                        "channel_name": channel_name,
                        "pins": [],
                    }
                )
            else:
                console.print("[yellow]No pinned messages found.[/yellow]")
            return

        # Collect user IDs for resolution
        user_ids: set[str] = set()
        for item in items:
            message = item.get("message", {})
            if user_id := message.get("user"):
                user_ids.add(user_id)
            if created_by := item.get("created_by"):
                user_ids.add(created_by)

        # Resolve user names
        users = slack.get_user_display_names(list(user_ids))

        # Get channel names from cache
        channels = slack.get_channel_names()

        # Format pins
        formatted_pins = [_format_pinned_item(item, users, channels) for item in items]

        if output_json_flag:
            output_json(
                {
                    "channel": channel_id,
                    "channel_name": channel_name,
                    "pins": formatted_pins,
                }
            )
        else:
            console.print(f"[dim]Found {len(formatted_pins)} pinned messages[/dim]\n")
            for pin in formatted_pins:
                user_name = format_user_name(pin["user_name"], pin["user_id"])
                print(f"{pin['datetime']}  {user_name}")

                # Truncate text for preview (first 200 chars)
                text = pin["text"]
                if len(text) > 200:
                    text = text[:200] + "..."
                print(format_message_text(text))

                # Show who pinned it and when
                if pin["pinned_by_name"]:
                    pinned_at = ""
                    if pin["pinned_at"]:
                        try:
                            dt = datetime.fromtimestamp(pin["pinned_at"], tz=timezone.utc)
                            pinned_at = f" on {dt.strftime('%Y-%m-%d')}"
                        except (ValueError, OSError):
                            pass
                    console.print(f"  [dim]Pinned by @{pin['pinned_by_name']}{pinned_at}[/dim]")

                console.print(f"  [dim]ts={pin['ts']}[/dim]")
                print()  # Blank line between pins

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None


@app.command("add")
def add_pin(
    channel: Annotated[
        str,
        typer.Argument(
            help="Channel reference (#channel-name or channel ID).",
        ),
    ],
    timestamp: Annotated[
        str,
        typer.Argument(
            help="Message timestamp (ts) to pin.",
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
    """Pin a message to a channel.

    Examples:
        slack pins add '#general' 1234567890.123456
        slack pins add C0123456789 1234567890.123456
    """
    # Get org context
    ctx = get_context()
    slack = ctx.get_slack_client()

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # Pin message
    try:
        if not output_json_flag:
            console.print(f"[dim]Pinning message {timestamp} in #{channel_name}...[/dim]")

        result = slack.pin_message(channel_id, timestamp)

        if output_json_flag:
            output_json(result)
        else:
            console.print("[green]Message pinned successfully.[/green]")
            console.print(f"[dim]ts={timestamp}[/dim]")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None


@app.command("remove")
def remove_pin(
    channel: Annotated[
        str,
        typer.Argument(
            help="Channel reference (#channel-name or channel ID).",
        ),
    ],
    timestamp: Annotated[
        str,
        typer.Argument(
            help="Message timestamp (ts) to unpin.",
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
    """Unpin a message from a channel.

    Examples:
        slack pins remove '#general' 1234567890.123456
        slack pins remove C0123456789 1234567890.123456
    """
    # Get org context
    ctx = get_context()
    slack = ctx.get_slack_client()

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # Unpin message
    try:
        if not output_json_flag:
            console.print(f"[dim]Unpinning message {timestamp} from #{channel_name}...[/dim]")

        result = slack.unpin_message(channel_id, timestamp)

        if output_json_flag:
            output_json(result)
        else:
            console.print("[green]Message unpinned successfully.[/green]")
            console.print(f"[dim]ts={timestamp}[/dim]")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None
