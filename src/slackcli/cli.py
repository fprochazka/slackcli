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
    files,
    messages,
    pins,
    reactions,
    resolve,
    scheduled,
    search,
    users,
)

app.add_typer(conversations.app, name="conversations")
app.add_typer(messages.app, name="messages")
app.add_typer(reactions.app, name="reactions")
app.add_typer(pins.app, name="pins")
app.add_typer(scheduled.app, name="scheduled")
app.add_typer(search.app, name="search")
app.add_typer(users.app, name="users")
app.add_typer(files.app, name="files")
app.command("resolve")(resolve.resolve_command)


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
    import os

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

    # Show which org would be used
    env_org = os.environ.get("SLACK_ORG")
    cli_org = _ctx.org_name

    # Typer merges env var into the option, so if cli_org matches env_org,
    # it likely came from the environment variable
    if cli_org and env_org and cli_org == env_org:
        console.print(f"[dim]Using org from SLACK_ORG: {cli_org}[/dim]")
    elif cli_org:
        console.print(f"[dim]Using org from --org: {cli_org}[/dim]")
    elif config.default_org:
        console.print(f"[dim]Using default org: {config.default_org}[/dim]")
    else:
        console.print("[dim]No org selected (use --org or SLACK_ORG)[/dim]")


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
