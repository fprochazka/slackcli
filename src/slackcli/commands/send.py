"""Send command for Slack CLI."""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from slack_sdk.errors import SlackApiError

from ..context import get_context
from ..logging import console, error_console, get_logger
from ..output import output_json
from .messages import resolve_channel

logger = get_logger(__name__)


def send_command(
    channel: Annotated[
        str,
        typer.Argument(
            help="Channel reference (#channel-name or channel ID).",
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
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output the posted message details as JSON.",
        ),
    ] = False,
) -> None:
    """Send a message to a Slack channel or thread.

    Examples:
        slack send '#general' "Hello world"
        slack send '#general' --thread 1234567890.123456 "Reply in thread"
        echo "Hello" | slack send '#general' --stdin
        cat message.txt | slack send '#general' --stdin
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

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # Send message
    try:
        if not output_json_flag:
            if thread:
                console.print(f"[dim]Sending reply to thread {thread} in #{channel_name}...[/dim]")
            else:
                console.print(f"[dim]Sending message to #{channel_name}...[/dim]")

        result = slack.send_message(channel_id, message, thread_ts=thread)

        if output_json_flag:
            output_json(result)
        else:
            ts = result.get("ts", "unknown")
            console.print("[green]Message sent successfully.[/green]")
            console.print(f"[dim]ts={ts}[/dim]")

    except SlackApiError as e:
        error = e.response.get("error", str(e))
        error_console.print(f"[red]Slack API error: {error}[/red]")

        # Provide helpful hints for common errors
        if error == "not_in_channel":
            error_console.print("[dim]Hint: The bot/user must be a member of this channel.[/dim]")
        elif error == "channel_not_found":
            error_console.print("[dim]Hint: The channel may not exist or you don't have access to it.[/dim]")
        elif error == "is_archived":
            error_console.print("[dim]Hint: This channel is archived and cannot receive messages.[/dim]")
        elif error == "msg_too_long":
            error_console.print("[dim]Hint: Message exceeds Slack's 40,000 character limit.[/dim]")
        elif error == "no_text":
            error_console.print("[dim]Hint: Message text cannot be empty.[/dim]")
        elif error == "rate_limited":
            retry_after = e.response.headers.get("Retry-After", "unknown")
            error_console.print(f"[dim]Hint: Rate limited. Try again in {retry_after} seconds.[/dim]")

        raise typer.Exit(1) from None
