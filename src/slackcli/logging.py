"""Logging configuration for Slack CLI."""

import logging

from rich.console import Console
from rich.logging import RichHandler

console = Console()
error_console = Console(stderr=True)


def setup_logging(verbose: bool = False) -> None:
    """Configure logging for the CLI.

    Args:
        verbose: If True, set log level to DEBUG, otherwise INFO.
    """
    level = logging.DEBUG if verbose else logging.INFO

    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[
            RichHandler(
                console=error_console,
                rich_tracebacks=True,
                show_time=verbose,
                show_path=verbose,
            )
        ],
    )

    # Reduce noise from third-party libraries unless in verbose mode
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("slack_sdk").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger with the given name.

    Args:
        name: The name of the logger (typically __name__).

    Returns:
        A configured logger instance.
    """
    return logging.getLogger(name)
