"""Edit command for Slack CLI."""

from __future__ import annotations

from typing import Annotated

import typer
from slack_sdk.errors import SlackApiError

from ..context import get_context
from ..errors import format_error_with_hint
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
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None
