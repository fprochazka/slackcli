"""Direct message command for Slack CLI."""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from slack_sdk.errors import SlackApiError

from ..context import get_context
from ..errors import format_error_with_hint
from ..logging import console, error_console, get_logger
from ..output import output_json

logger = get_logger(__name__)


def dm_command(
    user: Annotated[
        str,
        typer.Argument(
            help="User reference (@username, @email@example.com, or user ID).",
        ),
    ],
    message: Annotated[
        str | None,
        typer.Argument(
            help="Message text to send. Use --stdin to read from stdin instead.",
        ),
    ] = None,
    stdin: Annotated[
        bool,
        typer.Option(
            "--stdin",
            help="Read message text from stdin.",
        ),
    ] = False,
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output the posted message details as JSON.",
        ),
    ] = False,
) -> None:
    """Send a direct message to a Slack user.

    Examples:
        slack dm '@john.doe' "Hello!"
        slack dm 'U0123456789' "Hello!"
        slack dm '@john@example.com' "Hello via email lookup!"
        echo "Hello" | slack dm '@john.doe' --stdin
    """
    # Validate message input
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
    elif message is None:
        error_console.print("[red]Message text is required. Provide it as an argument or use --stdin.[/red]")
        raise typer.Exit(1)

    # Get org context
    cli_ctx = get_context()
    slack = cli_ctx.get_slack_client()

    # Resolve user
    resolved = slack.resolve_user(user)
    if resolved is None:
        error_console.print(f"[red]Could not resolve user '{user}'.[/red]")
        error_console.print("[dim]Hint: Try @username, @email@example.com, or a raw user ID (U...).[/dim]")
        raise typer.Exit(1)

    user_id, username = resolved
    logger.debug(f"Resolved user '{user}' to '{user_id}' (@{username})")

    # Open DM conversation
    try:
        if not output_json_flag:
            console.print(f"[dim]Opening DM with @{username}...[/dim]")

        dm_channel = slack.open_dm(user_id)
        dm_channel_id = dm_channel.get("channel", {}).get("id")

        if not dm_channel_id:
            error_console.print("[red]Failed to open DM channel.[/red]")
            raise typer.Exit(1)

        logger.debug(f"Opened DM channel {dm_channel_id} with user {user_id}")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]Failed to open DM: {error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")
        raise typer.Exit(1) from None

    # Send message
    try:
        if not output_json_flag:
            console.print(f"[dim]Sending DM to @{username}...[/dim]")

        result = slack.send_message(dm_channel_id, message)

        if output_json_flag:
            output_json(
                {
                    "ok": True,
                    "user_id": user_id,
                    "username": username,
                    "channel": dm_channel_id,
                    "ts": result.get("ts"),
                    "message": result.get("message"),
                }
            )
        else:
            ts = result.get("ts", "unknown")
            console.print(f"[green]DM sent to @{username} successfully.[/green]")
            console.print(f"[dim]ts={ts}[/dim]")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None
