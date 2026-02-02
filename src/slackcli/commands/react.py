"""React and unreact commands for Slack CLI."""

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


def strip_emoji_colons(emoji: str) -> str:
    """Strip surrounding colons from an emoji name.

    Args:
        emoji: The emoji name, with or without colons.

    Returns:
        The emoji name without colons.

    Examples:
        strip_emoji_colons(":thumbsup:") -> "thumbsup"
        strip_emoji_colons("thumbsup") -> "thumbsup"
        strip_emoji_colons(":+1:") -> "+1"
    """
    if emoji.startswith(":") and emoji.endswith(":") and len(emoji) > 2:
        return emoji[1:-1]
    return emoji


def react_command(
    channel: Annotated[
        str,
        typer.Argument(
            help="Channel reference (#channel-name or channel ID).",
        ),
    ],
    timestamp: Annotated[
        str,
        typer.Argument(
            help="Message timestamp (ts) to react to.",
        ),
    ],
    emoji: Annotated[
        str,
        typer.Argument(
            help="Emoji name (e.g., 'thumbsup' or ':thumbsup:').",
        ),
    ],
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output the reaction details as JSON.",
        ),
    ] = False,
) -> None:
    """Add an emoji reaction to a message.

    Examples:
        slack react '#general' 1234567890.123456 thumbsup
        slack react '#general' 1234567890.123456 :+1:
        slack react C0123456789 1234567890.123456 heart
    """
    # Strip colons from emoji name
    emoji_name = strip_emoji_colons(emoji)

    if not emoji_name:
        error_console.print("[red]Emoji name cannot be empty.[/red]")
        raise typer.Exit(1)

    # Get org context
    cli_ctx = get_context()
    slack = cli_ctx.get_slack_client()

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # Add reaction
    try:
        if not output_json_flag:
            console.print(f"[dim]Adding :{emoji_name}: to message {timestamp} in #{channel_name}...[/dim]")

        result = slack.add_reaction(channel_id, timestamp, emoji_name)

        if output_json_flag:
            output_json(result)
        else:
            console.print(f"[green]Reaction :{emoji_name}: added successfully.[/green]")
            console.print(f"[dim]ts={timestamp}[/dim]")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e, context={"emoji": emoji_name})
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None


def unreact_command(
    channel: Annotated[
        str,
        typer.Argument(
            help="Channel reference (#channel-name or channel ID).",
        ),
    ],
    timestamp: Annotated[
        str,
        typer.Argument(
            help="Message timestamp (ts) to remove reaction from.",
        ),
    ],
    emoji: Annotated[
        str,
        typer.Argument(
            help="Emoji name (e.g., 'thumbsup' or ':thumbsup:').",
        ),
    ],
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output the reaction details as JSON.",
        ),
    ] = False,
) -> None:
    """Remove an emoji reaction from a message.

    Examples:
        slack unreact '#general' 1234567890.123456 thumbsup
        slack unreact '#general' 1234567890.123456 :+1:
        slack unreact C0123456789 1234567890.123456 heart
    """
    # Strip colons from emoji name
    emoji_name = strip_emoji_colons(emoji)

    if not emoji_name:
        error_console.print("[red]Emoji name cannot be empty.[/red]")
        raise typer.Exit(1)

    # Get org context
    cli_ctx = get_context()
    slack = cli_ctx.get_slack_client()

    # Resolve channel
    channel_id, channel_name = resolve_channel(slack, channel)
    logger.debug(f"Resolved channel '{channel}' to '{channel_id}'")

    # Remove reaction
    try:
        if not output_json_flag:
            console.print(f"[dim]Removing :{emoji_name}: from message {timestamp} in #{channel_name}...[/dim]")

        result = slack.remove_reaction(channel_id, timestamp, emoji_name)

        if output_json_flag:
            output_json(result)
        else:
            console.print(f"[green]Reaction :{emoji_name}: removed successfully.[/green]")
            console.print(f"[dim]ts={timestamp}[/dim]")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e, context={"emoji": emoji_name})
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

        raise typer.Exit(1) from None
