"""Search command group for Slack CLI."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Annotated, Any

import typer
from slack_sdk.errors import SlackApiError

from ..context import get_context
from ..errors import format_error_with_hint, get_error_code
from ..logging import console, error_console, get_logger
from ..output import output_json

if TYPE_CHECKING:
    from ..client import SlackCli

logger = get_logger(__name__)

app = typer.Typer(
    name="search",
    help="Search Slack messages and files.",
    no_args_is_help=True,
    rich_markup_mode=None,
)

# Helpful error message for missing search:read scope
SEARCH_SCOPE_ERROR = """
To enable search, add the 'search:read' scope to your Slack app:

1. Go to https://api.slack.com/apps
2. Select your app -> OAuth & Permissions
3. Under "User Token Scopes", add 'search:read'
4. Reinstall the app to your workspace to get a new token
5. Update your token in ~/.config/slackcli/config.toml
"""


def parse_date_spec(spec: str) -> str:
    """Parse a date specification into YYYY-MM-DD format for Slack search.

    Supports:
    - ISO date: "2024-01-15"
    - Relative: "7d", "30d"
    - Keywords: "today", "yesterday"

    Args:
        spec: The date specification string.

    Returns:
        Date string in YYYY-MM-DD format.

    Raises:
        ValueError: If spec cannot be parsed.
    """
    spec = spec.strip().lower()
    now = datetime.now(tz=timezone.utc)

    # Keywords
    if spec == "today":
        return now.strftime("%Y-%m-%d")
    if spec == "yesterday":
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # Relative time: 7d, 30d
    relative_match = re.match(r"^(\d+)d$", spec)
    if relative_match:
        days = int(relative_match.group(1))
        return (now - timedelta(days=days)).strftime("%Y-%m-%d")

    # ISO date: 2024-01-15
    try:
        dt = datetime.fromisoformat(spec)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        pass

    raise ValueError(f"Cannot parse date specification: {spec}")


def build_search_query(
    query: str,
    in_channel: str | None = None,
    from_user: str | None = None,
    before: str | None = None,
    after: str | None = None,
) -> str:
    """Build a Slack search query string with modifiers.

    Args:
        query: The base search query.
        in_channel: Filter by channel (with or without #).
        from_user: Filter by sender (with or without @).
        before: Before date (YYYY-MM-DD format).
        after: After date (YYYY-MM-DD format).

    Returns:
        Complete search query string.
    """
    parts = [query]

    if in_channel:
        # Remove # prefix if present
        channel = in_channel.lstrip("#")
        parts.append(f"in:{channel}")

    if from_user:
        # Remove @ prefix if present
        user = from_user.lstrip("@")
        parts.append(f"from:{user}")

    if before:
        parts.append(f"before:{before}")

    if after:
        parts.append(f"after:{after}")

    return " ".join(parts)


def format_message_url(
    workspace: str,
    channel_id: str,
    ts: str,
    thread_ts: str | None = None,
) -> str:
    """Format a Slack message URL.

    Args:
        workspace: The workspace name/domain.
        channel_id: The channel ID.
        ts: The message timestamp.
        thread_ts: Optional thread timestamp.

    Returns:
        The formatted message URL.
    """
    # Convert timestamp to URL format (remove dot)
    ts_url = ts.replace(".", "")
    base_url = f"https://{workspace}.slack.com/archives/{channel_id}/p{ts_url}"

    if thread_ts and thread_ts != ts:
        thread_ts_url = thread_ts.replace(".", "")
        base_url += f"?thread_ts={thread_ts_url}"

    return base_url


def handle_search_error(error: SlackApiError) -> None:
    """Handle a Slack API error from search, with special handling for missing_scope.

    Args:
        error: The SlackApiError.

    Raises:
        typer.Exit: Always exits after displaying error.
    """
    error_code = get_error_code(error)

    if error_code == "missing_scope":
        # Check if this is specifically about search:read
        needed = error.response.get("needed", "")
        if needed == "search:read" or "search" in str(error.response):
            error_console.print("[red]Error: Missing required OAuth scope: search:read[/red]")
            error_console.print(SEARCH_SCOPE_ERROR)
        else:
            error_msg, hint = format_error_with_hint(error)
            error_console.print(f"[red]{error_msg}[/red]")
            if hint:
                error_console.print(f"[dim]Hint: {hint}[/dim]")
    else:
        error_msg, hint = format_error_with_hint(error)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")

    raise typer.Exit(1)


def output_search_messages_text(
    results: dict[str, Any],
    query: str,
    slack: SlackCli,
) -> None:
    """Output message search results as formatted text.

    Args:
        results: The search results from the API.
        query: The search query.
        slack: The Slack client for workspace info.
    """
    matches = results.get("messages", {}).get("matches", [])
    total = results.get("messages", {}).get("total", 0)

    if not matches:
        console.print(f'[yellow]No messages found matching "{query}"[/yellow]')
        return

    console.print(f'Found {total} messages matching "{query}"\n')

    for match in matches:
        channel_info = match.get("channel", {})
        channel_name = channel_info.get("name", "unknown")

        ts = match.get("ts", "")
        user = match.get("username", match.get("user", "unknown"))
        text = match.get("text", "(no text)")

        # Parse timestamp
        try:
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            date_str = ts

        # Format the message URL
        permalink = match.get("permalink", "")

        # Display
        console.print(f"[bold]#{channel_name}[/bold]  {date_str}  @{user}")
        # Truncate long text for display
        display_text = text
        if len(display_text) > 200:
            display_text = display_text[:197] + "..."
        console.print(f"  {display_text}")
        if permalink:
            console.print(f"  [dim]{permalink}[/dim]")
        console.print()


def output_search_files_text(
    results: dict[str, Any],
    query: str,
) -> None:
    """Output file search results as formatted text.

    Args:
        results: The search results from the API.
        query: The search query.
    """
    matches = results.get("files", {}).get("matches", [])
    total = results.get("files", {}).get("total", 0)

    if not matches:
        console.print(f'[yellow]No files found matching "{query}"[/yellow]')
        return

    console.print(f'Found {total} files matching "{query}"\n')

    for match in matches:
        name = match.get("name", "unknown")
        title = match.get("title", "")
        filetype = match.get("filetype", "")
        size = match.get("size", 0)
        user = match.get("username", match.get("user", "unknown"))
        created = match.get("created", 0)
        permalink = match.get("permalink", "")

        # Format size
        if size < 1024:
            size_str = f"{size} B"
        elif size < 1024 * 1024:
            size_str = f"{size / 1024:.1f} KB"
        else:
            size_str = f"{size / (1024 * 1024):.1f} MB"

        # Format date
        try:
            dt = datetime.fromtimestamp(created, tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError):
            date_str = str(created)

        # Display
        display_name = title if title else name
        console.print(f"[bold]{display_name}[/bold]  ({filetype}, {size_str})")
        console.print(f"  Uploaded by @{user} on {date_str}")
        if permalink:
            console.print(f"  [dim]{permalink}[/dim]")
        console.print()


@app.command("messages")
def search_messages(
    query: Annotated[
        str,
        typer.Argument(
            help="Search query.",
        ),
    ],
    in_channel: Annotated[
        str | None,
        typer.Option(
            "--in",
            help="Filter by channel (#channel-name or channel name).",
        ),
    ] = None,
    from_user: Annotated[
        str | None,
        typer.Option(
            "--from",
            help="Filter by sender (@username or username).",
        ),
    ] = None,
    before: Annotated[
        str | None,
        typer.Option(
            "--before",
            help="Before date (YYYY-MM-DD, '7d', 'yesterday').",
        ),
    ] = None,
    after: Annotated[
        str | None,
        typer.Option(
            "--after",
            help="After date (YYYY-MM-DD, '7d', 'yesterday').",
        ),
    ] = None,
    sort: Annotated[
        str,
        typer.Option(
            "--sort",
            help="Sort by: 'score' or 'timestamp'.",
        ),
    ] = "score",
    sort_dir: Annotated[
        str,
        typer.Option(
            "--sort-dir",
            help="Sort direction: 'asc' or 'desc'.",
        ),
    ] = "desc",
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            "-n",
            help="Maximum number of results (max 100).",
        ),
    ] = 20,
    page: Annotated[
        int,
        typer.Option(
            "--page",
            help="Page number (1-indexed).",
        ),
    ] = 1,
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output raw JSON instead of formatted text.",
        ),
    ] = False,
) -> None:
    """Search for messages in Slack.

    Examples:
        slack search messages "quarterly report"
        slack search messages "bug fix" --in '#engineering'
        slack search messages "deadline" --from '@john.doe'
        slack search messages "meeting" --after 7d
        slack search messages "project" --before 2024-01-15 --after 2024-01-01
    """
    # Validate options
    if sort not in ("score", "timestamp"):
        error_console.print(f"[red]Invalid --sort value: {sort}. Use 'score' or 'timestamp'.[/red]")
        raise typer.Exit(1)

    if sort_dir not in ("asc", "desc"):
        error_console.print(f"[red]Invalid --sort-dir value: {sort_dir}. Use 'asc' or 'desc'.[/red]")
        raise typer.Exit(1)

    if limit < 1 or limit > 100:
        error_console.print("[red]--limit must be between 1 and 100.[/red]")
        raise typer.Exit(1)

    if page < 1:
        error_console.print("[red]--page must be at least 1.[/red]")
        raise typer.Exit(1)

    # Parse date options
    before_date = None
    after_date = None

    if before:
        try:
            before_date = parse_date_spec(before)
        except ValueError as e:
            error_console.print(f"[red]Invalid --before value: {e}[/red]")
            raise typer.Exit(1) from None

    if after:
        try:
            after_date = parse_date_spec(after)
        except ValueError as e:
            error_console.print(f"[red]Invalid --after value: {e}[/red]")
            raise typer.Exit(1) from None

    # Build the full query
    full_query = build_search_query(
        query,
        in_channel=in_channel,
        from_user=from_user,
        before=before_date,
        after=after_date,
    )

    logger.debug(f"Search query: {full_query}")

    # Get org context
    ctx = get_context()
    slack = ctx.get_slack_client()

    if not output_json_flag:
        console.print(f"[dim]Searching for messages: {full_query}...[/dim]")

    # Perform search
    try:
        results = slack.search_messages(
            query=full_query,
            sort=sort,
            sort_dir=sort_dir,
            count=limit,
            page=page,
        )
    except SlackApiError as e:
        handle_search_error(e)

    # Output results
    if output_json_flag:
        output_json(results)
    else:
        output_search_messages_text(results, query, slack)


@app.command("files")
def search_files(
    query: Annotated[
        str,
        typer.Argument(
            help="Search query.",
        ),
    ],
    in_channel: Annotated[
        str | None,
        typer.Option(
            "--in",
            help="Filter by channel (#channel-name or channel name).",
        ),
    ] = None,
    from_user: Annotated[
        str | None,
        typer.Option(
            "--from",
            help="Filter by uploader (@username or username).",
        ),
    ] = None,
    before: Annotated[
        str | None,
        typer.Option(
            "--before",
            help="Before date (YYYY-MM-DD, '7d', 'yesterday').",
        ),
    ] = None,
    after: Annotated[
        str | None,
        typer.Option(
            "--after",
            help="After date (YYYY-MM-DD, '7d', 'yesterday').",
        ),
    ] = None,
    sort: Annotated[
        str,
        typer.Option(
            "--sort",
            help="Sort by: 'score' or 'timestamp'.",
        ),
    ] = "score",
    sort_dir: Annotated[
        str,
        typer.Option(
            "--sort-dir",
            help="Sort direction: 'asc' or 'desc'.",
        ),
    ] = "desc",
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            "-n",
            help="Maximum number of results (max 100).",
        ),
    ] = 20,
    page: Annotated[
        int,
        typer.Option(
            "--page",
            help="Page number (1-indexed).",
        ),
    ] = 1,
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output raw JSON instead of formatted text.",
        ),
    ] = False,
) -> None:
    """Search for files in Slack.

    Examples:
        slack search files "report.pdf"
        slack search files "spreadsheet" --in '#finance'
        slack search files "presentation" --from '@jane.doe'
        slack search files "budget" --after 30d
    """
    # Validate options
    if sort not in ("score", "timestamp"):
        error_console.print(f"[red]Invalid --sort value: {sort}. Use 'score' or 'timestamp'.[/red]")
        raise typer.Exit(1)

    if sort_dir not in ("asc", "desc"):
        error_console.print(f"[red]Invalid --sort-dir value: {sort_dir}. Use 'asc' or 'desc'.[/red]")
        raise typer.Exit(1)

    if limit < 1 or limit > 100:
        error_console.print("[red]--limit must be between 1 and 100.[/red]")
        raise typer.Exit(1)

    if page < 1:
        error_console.print("[red]--page must be at least 1.[/red]")
        raise typer.Exit(1)

    # Parse date options
    before_date = None
    after_date = None

    if before:
        try:
            before_date = parse_date_spec(before)
        except ValueError as e:
            error_console.print(f"[red]Invalid --before value: {e}[/red]")
            raise typer.Exit(1) from None

    if after:
        try:
            after_date = parse_date_spec(after)
        except ValueError as e:
            error_console.print(f"[red]Invalid --after value: {e}[/red]")
            raise typer.Exit(1) from None

    # Build the full query
    full_query = build_search_query(
        query,
        in_channel=in_channel,
        from_user=from_user,
        before=before_date,
        after=after_date,
    )

    logger.debug(f"Search query: {full_query}")

    # Get org context
    ctx = get_context()
    slack = ctx.get_slack_client()

    if not output_json_flag:
        console.print(f"[dim]Searching for files: {full_query}...[/dim]")

    # Perform search
    try:
        results = slack.search_files(
            query=full_query,
            sort=sort,
            sort_dir=sort_dir,
            count=limit,
            page=page,
        )
    except SlackApiError as e:
        handle_search_error(e)

    # Output results
    if output_json_flag:
        output_json(results)
    else:
        output_search_files_text(results, query)
