"""Edit command for Slack CLI."""

from __future__ import annotations

from typing import Annotated

import typer
from slack_sdk.errors import SlackApiError

from ..context import get_context
from ..logging import console, error_console, get_logger
from ..output import output_json
from .messages import resolve_channel

logger = get_logger(__name__)


def edit_command(
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
        slack edit '#general' 1234567890.123456 "Updated message"
        slack edit C0123456789 1234567890.123456 "Fixed typo"
    """
    # Validate message text
    if not message.strip():
        error_console.print("[red]Message text cannot be empty.[/red]")
        raise typer.Exit(1)

    # Get org context
    cli_ctx = get_context()
    slack = cli_ctx.get_slack_client()

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
        error = e.response.get("error", str(e))
        error_console.print(f"[red]Slack API error: {error}[/red]")

        # Provide helpful hints for common errors
        if error == "message_not_found":
            error_console.print("[dim]Hint: The message with this timestamp was not found.[/dim]")
        elif error == "cant_update_message":
            error_console.print("[dim]Hint: You can only edit your own messages.[/dim]")
        elif error == "edit_window_closed":
            error_console.print("[dim]Hint: The edit window for this message has expired.[/dim]")
        elif error == "not_in_channel":
            error_console.print("[dim]Hint: The bot/user must be a member of this channel.[/dim]")
        elif error == "channel_not_found":
            error_console.print("[dim]Hint: The channel may not exist or you don't have access to it.[/dim]")
        elif error == "msg_too_long":
            error_console.print("[dim]Hint: Message exceeds Slack's 40,000 character limit.[/dim]")
        elif error == "no_text":
            error_console.print("[dim]Hint: Message text cannot be empty.[/dim]")
        elif error == "rate_limited":
            retry_after = e.response.headers.get("Retry-After", "unknown")
            error_console.print(f"[dim]Hint: Rate limited. Try again in {retry_after} seconds.[/dim]")

        raise typer.Exit(1) from None
