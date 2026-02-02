"""Send command for Slack CLI."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from slack_sdk.errors import SlackApiError

from ..context import get_context
from ..errors import format_error_with_hint
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
    """Send a message to a Slack channel or thread.

    Examples:
        slack send '#general' "Hello world"
        slack send '#general' --thread 1234567890.123456 "Reply in thread"
        echo "Hello" | slack send '#general' --stdin
        cat message.txt | slack send '#general' --stdin
        slack send '#general' --file ./report.pdf
        slack send '#general' "Here's the report" --file ./report.pdf
        slack send '#general' --file ./a.csv --file ./b.csv
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
    cli_ctx = get_context()
    slack = cli_ctx.get_slack_client()

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    try:
        results: dict = {
            "ok": True,
            "channel": channel_id,
            "channel_name": channel_name,
        }

        # Send message first if we have one (and we have files)
        # If no files, just send the message normally
        # If files but no message, the first file gets the "initial_comment" treatment
        if message and has_files:
            # Send message first, then upload files (files will be separate from message)
            if not output_json_flag:
                if thread:
                    console.print(f"[dim]Sending reply to thread {thread} in #{channel_name}...[/dim]")
                else:
                    console.print(f"[dim]Sending message to #{channel_name}...[/dim]")

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
                    console.print(f"[dim]Sending reply to thread {thread} in #{channel_name}...[/dim]")
                else:
                    console.print(f"[dim]Sending message to #{channel_name}...[/dim]")

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
