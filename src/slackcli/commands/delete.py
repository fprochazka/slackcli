"""Delete command for Slack CLI."""

from __future__ import annotations

from typing import Annotated

import typer
from slack_sdk.errors import SlackApiError

from ..context import get_context
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
        error = e.response.get("error", str(e))
        error_console.print(f"[red]Slack API error: {error}[/red]")

        # Provide helpful hints for common errors
        if error == "message_not_found":
            error_console.print("[dim]Hint: The message with this timestamp was not found.[/dim]")
        elif error == "cant_delete_message":
            error_console.print("[dim]Hint: You can only delete your own messages, or you need admin privileges.[/dim]")
        elif error == "compliance_exports_prevent_deletion":
            error_console.print("[dim]Hint: Compliance exports are enabled, preventing message deletion.[/dim]")
        elif error == "not_in_channel":
            error_console.print("[dim]Hint: The bot/user must be a member of this channel.[/dim]")
        elif error == "channel_not_found":
            error_console.print("[dim]Hint: The channel may not exist or you don't have access to it.[/dim]")
        elif error == "rate_limited":
            retry_after = e.response.headers.get("Retry-After", "unknown")
            error_console.print(f"[dim]Hint: Rate limited. Try again in {retry_after} seconds.[/dim]")

        raise typer.Exit(1) from None
