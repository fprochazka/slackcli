"""Delete command for Slack CLI."""

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


def delete_command(
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
        slack delete '#general' 1234567890.123456
        slack delete C0123456789 1234567890.123456 --force
    """
    # Get org context
    cli_ctx = get_context()
    slack = cli_ctx.get_slack_client()

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
