"""Download command for Slack CLI."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

import typer
from slack_sdk.errors import SlackApiError

from ..context import get_context
from ..errors import format_error_with_hint
from ..logging import console, error_console, get_logger
from ..output import output_json

logger = get_logger(__name__)

# Default download directory
DEFAULT_DOWNLOAD_DIR = Path("/tmp/slackcli")


def parse_file_url(url: str) -> tuple[str | None, str | None]:
    """Parse a Slack file URL to extract file ID and optional org.

    Supports formats:
    - https://files.slack.com/files-pri/T0XXX-F0XXX/download/filename.ext
    - https://workspace.slack.com/files/U0XXX/F0XXX/filename.ext

    Args:
        url: The Slack file URL.

    Returns:
        Tuple of (file_id, org_name) where org_name may be None.
    """
    # Match files.slack.com URL format
    files_match = re.match(
        r"https://files\.slack\.com/files-pri/([A-Z0-9]+)-([A-Z0-9]+)/",
        url,
    )
    if files_match:
        return files_match.group(2), None

    # Match workspace.slack.com/files URL format
    workspace_match = re.match(
        r"https://([a-z0-9-]+)\.slack\.com/files/[A-Z0-9]+/([A-Z0-9]+)/",
        url,
    )
    if workspace_match:
        return workspace_match.group(2), workspace_match.group(1)

    return None, None


def download_command(
    url_or_id: Annotated[
        str,
        typer.Argument(
            help="File URL or file ID to download.",
        ),
    ],
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output path. If a directory, uses original filename. Default: /tmp/slackcli/",
        ),
    ] = None,
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output download details as JSON.",
        ),
    ] = False,
) -> None:
    """Download a file from Slack.

    Files can be specified by URL or file ID. The URL can be obtained from
    the `slack messages` command output or from Slack's web interface.

    Examples:
        slack download F0ABC123DEF
        slack download https://files.slack.com/files-pri/T0XXX-F0XXX/download/file.txt
        slack download F0ABC123DEF --output /tmp/myfile.txt
        slack download F0ABC123DEF --output ./downloads/
    """
    # Get org context
    cli_ctx = get_context()
    slack = cli_ctx.get_slack_client()

    # Check if input is a URL or file ID
    file_id: str | None = None
    download_url: str | None = None

    if url_or_id.startswith("https://"):
        # It's a URL - try to parse file ID and extract download URL
        file_id, _ = parse_file_url(url_or_id)
        if file_id is None:
            error_console.print(f"[red]Could not parse file ID from URL: {url_or_id}[/red]")
            raise typer.Exit(1)
    else:
        # Assume it's a file ID
        file_id = url_or_id

    # Get file info to get the download URL and filename
    try:
        if not output_json_flag:
            console.print(f"[dim]Getting file info for {file_id}...[/dim]")

        file_info_result = slack.get_file_info(file_id)
        file_info = file_info_result.get("file", {})

        download_url = file_info.get("url_private_download")
        if not download_url:
            error_console.print(f"[red]File {file_id} has no download URL.[/red]")
            raise typer.Exit(1)

        filename = file_info.get("name", file_id)
        file_size = file_info.get("size", 0)

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")
        raise typer.Exit(1) from None

    # Determine output path
    if output is None:
        # Use default directory with original filename
        output_path = DEFAULT_DOWNLOAD_DIR / filename
    elif output.is_dir() or str(output).endswith("/"):
        # Directory specified, use original filename
        output_path = output / filename
    else:
        # Full path specified
        output_path = output

    # Download the file
    try:
        if not output_json_flag:
            size_str = _format_size(file_size)
            console.print(f"[dim]Downloading {filename} ({size_str})...[/dim]")

        result = slack.download_file(download_url, str(output_path))

        if output_json_flag:
            output_json(
                {
                    "ok": True,
                    "file_id": file_id,
                    "filename": filename,
                    "path": result["path"],
                    "size": result["size"],
                }
            )
        else:
            size_str = _format_size(result["size"])
            console.print(f"[green]Downloaded: {result['path']} ({size_str})[/green]")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")
        raise typer.Exit(1) from None


def _format_size(size: int) -> str:
    """Format file size for display."""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.1f} GB"
