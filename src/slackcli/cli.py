"""CLI entry point for Slack CLI."""

import sys
from pathlib import Path
from typing import Annotated

import typer

from . import __version__
from .config import get_config_path, load_config
from .context import get_context
from .logging import console, error_console, get_logger, setup_logging

app = typer.Typer(
    name="slack",
    help="A command-line interface for Slack.",
    no_args_is_help=True,
    rich_markup_mode=None,
)

logger = get_logger(__name__)

# Global context reference (for use in this module)
_ctx = get_context()

# Register command groups (imported here to avoid circular imports)
from .commands import (  # noqa: E402
    conversations,
    delete,
    dm,
    download,
    edit,
    messages,
    pins,
    react,
    resolve,
    schedule,
    send,
    unread,
)

app.add_typer(conversations.app, name="conversations")
app.command("messages")(messages.messages_command)
app.command("resolve")(resolve.resolve_command)
app.command("send")(send.send_command)
app.command("download")(download.download_command)
app.command("dm")(dm.dm_command)
app.command("edit")(edit.edit_command)
app.command("delete")(delete.delete_command)
app.command("react")(react.react_command)
app.command("unreact")(react.unreact_command)
app.command("unread")(unread.unread_command)
app.command("pin")(pins.pin_command)
app.command("unpin")(pins.unpin_command)
app.command("pins")(pins.pins_command)
app.command("schedule")(schedule.schedule_command)
app.add_typer(schedule.scheduled_app, name="scheduled")


def version_callback(value: bool) -> None:
    """Print version and exit."""
    if value:
        console.print(f"slack {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    org: Annotated[
        str | None,
        typer.Option(
            "--org",
            "-o",
            help="Organization name from config to use.",
            envvar="SLACK_ORG",
        ),
    ] = None,
    config_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to config file.",
            envvar="SLACK_CONFIG",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option(
            "--verbose",
            "-v",
            help="Enable debug logging.",
            is_eager=True,
        ),
    ] = False,
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-V",
            help="Show version and exit.",
            callback=version_callback,
            is_eager=True,
        ),
    ] = None,
) -> None:
    """Slack CLI - A command-line interface for Slack."""
    setup_logging(verbose=verbose)
    logger.debug("Debug logging enabled")

    _ctx.verbose = verbose
    _ctx.org_name = org

    # Load config if path specified or if we need it later
    if config_path:
        try:
            _ctx.config = load_config(config_path)
        except FileNotFoundError as e:
            error_console.print(f"[red]{e}[/red]")
            raise typer.Exit(1) from None


@app.command("config")
def show_config() -> None:
    """Show the current configuration."""
    import json

    config_path = get_config_path()

    if not config_path.exists():
        error_console.print(f"[yellow]Config file not found: {config_path}[/yellow]")
        raise typer.Exit(1)

    try:
        config = load_config()
    except Exception as e:
        error_console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1) from None

    config_display = {
        "default_org": config.default_org,
        "orgs": {
            name: {
                "token": org.token[:20] + "..." if len(org.token) > 20 else org.token,
            }
            for name, org in config.orgs.items()
        },
    }

    console.print(json.dumps(config_display, indent=2))
    console.print(f"\n[dim]Config file: {config_path}[/dim]")


def cli() -> None:
    """Main entry point for the CLI."""
    try:
        app()
    except Exception as e:
        error_console.print(f"[red]Error: {e}[/red]")
        if _ctx.verbose:
            error_console.print_exception()
        sys.exit(1)


if __name__ == "__main__":
    cli()
