"""User management commands for Slack CLI."""

from __future__ import annotations

from typing import Annotated

import typer
from slack_sdk.errors import SlackApiError

from ..context import get_context
from ..errors import format_error_with_hint
from ..logging import console, error_console, get_logger
from ..output import output_json
from ..users import (
    UserInfo,
    fetch_all_users_from_api,
    get_user,
    load_all_users_from_cache,
    resolve_user,
)

logger = get_logger(__name__)

app = typer.Typer(
    name="users",
    help="Manage Slack users.",
    no_args_is_help=True,
    rich_markup_mode=None,
)


def _user_to_dict(user: UserInfo) -> dict:
    """Convert a UserInfo to a dictionary for JSON output.

    Args:
        user: The UserInfo to convert.

    Returns:
        Dictionary suitable for JSON serialization.
    """
    return {
        "id": user.id,
        "name": user.name,
        "display_name": user.display_name,
        "real_name": user.real_name,
        "email": user.email,
        "is_bot": user.is_bot,
        "is_admin": user.is_admin,
        "deleted": user.deleted,
    }


def _format_user_line(user: UserInfo) -> str:
    """Format a user for text output.

    Args:
        user: The UserInfo to format.

    Returns:
        Formatted string for text display.
    """
    parts = [f"@{user.name}"]

    # Add display name if different from username
    if user.display_name and user.display_name != user.name:
        parts.append(f"({user.display_name})")

    # Add email if available
    if user.email:
        parts.append(f"<{user.email}>")

    # Add status indicators
    indicators = []
    if user.is_bot:
        indicators.append("bot")
    if user.is_admin:
        indicators.append("admin")
    if user.deleted:
        indicators.append("deleted")
    if indicators:
        parts.append(f"[{', '.join(indicators)}]")

    return " ".join(parts)


@app.command("list")
def list_users(
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            "-r",
            help="Force refresh the cache from Slack API.",
        ),
    ] = False,
    include_bots: Annotated[
        bool,
        typer.Option(
            "--bots",
            help="Include bot users in the list.",
        ),
    ] = False,
    include_deleted: Annotated[
        bool,
        typer.Option(
            "--deleted",
            help="Include deleted users in the list.",
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
    """List all users in the workspace.

    Examples:
        slack users list
        slack users list --refresh
        slack users list --bots --deleted
        slack users list --json
    """
    ctx = get_context()
    slack = ctx.get_slack_client()

    try:
        if refresh:
            if not output_json_flag:
                console.print("[dim]Fetching users from Slack API...[/dim]")
            users = fetch_all_users_from_api(slack)
            if not output_json_flag:
                console.print("[green]User cache updated successfully[/green]\n")
        else:
            # Try to load from cache first
            users = load_all_users_from_cache(slack.org_name)
            if not users:
                if not output_json_flag:
                    console.print("[dim]No cached users found, fetching from Slack API...[/dim]")
                users = fetch_all_users_from_api(slack)
                if not output_json_flag:
                    console.print("[green]User cache updated successfully[/green]\n")
            else:
                if not output_json_flag:
                    console.print("[dim]Using cached users. Use --refresh to update from Slack API[/dim]\n")

        # Filter users
        filtered_users = users
        if not include_bots:
            filtered_users = [u for u in filtered_users if not u.is_bot]
        if not include_deleted:
            filtered_users = [u for u in filtered_users if not u.deleted]

        # Sort by username
        filtered_users.sort(key=lambda u: u.name.lower())

        if output_json_flag:
            output_json(
                {
                    "users": [_user_to_dict(u) for u in filtered_users],
                    "count": len(filtered_users),
                }
            )
        else:
            for user in filtered_users:
                print(f"{user.id}: {_format_user_line(user)}")

            console.print(f"\n[dim]Total: {len(filtered_users)} users[/dim]")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")
        raise typer.Exit(1) from None


@app.command("search")
def search_users(
    query: Annotated[
        str,
        typer.Argument(
            help="Search query (matches name, display name, real name, or email).",
        ),
    ],
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output the result as JSON.",
        ),
    ] = False,
) -> None:
    """Search users by name or email.

    The search is case-insensitive and matches against:
    - username (@name)
    - display name
    - real name
    - email

    Examples:
        slack users search "john"
        slack users search "john@example.com"
        slack users search "john" --json
    """
    ctx = get_context()
    slack = ctx.get_slack_client()

    try:
        # Load users from cache, fetch if not available
        users = load_all_users_from_cache(slack.org_name)
        if not users:
            if not output_json_flag:
                console.print("[dim]No cached users found, fetching from Slack API...[/dim]")
            users = fetch_all_users_from_api(slack)
            if not output_json_flag:
                console.print("[green]User cache updated successfully[/green]\n")

        # Search locally
        query_lower = query.lower()
        matching_users = []
        for user in users:
            if (
                query_lower in user.name.lower()
                or query_lower in user.display_name.lower()
                or query_lower in user.real_name.lower()
                or (user.email and query_lower in user.email.lower())
            ):
                matching_users.append(user)

        # Sort by username
        matching_users.sort(key=lambda u: u.name.lower())

        if output_json_flag:
            output_json(
                {
                    "query": query,
                    "users": [_user_to_dict(u) for u in matching_users],
                    "count": len(matching_users),
                }
            )
        else:
            if not matching_users:
                console.print(f"[yellow]No users found matching '{query}'[/yellow]")
                return

            for user in matching_users:
                print(f"{user.id}: {_format_user_line(user)}")

            console.print(f"\n[dim]Found {len(matching_users)} users matching '{query}'[/dim]")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")
        raise typer.Exit(1) from None


@app.command("get")
def get_user_command(
    user_ref: Annotated[
        str,
        typer.Argument(
            help="User reference (@username, email, or user ID).",
        ),
    ],
    output_json_flag: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output the result as JSON.",
        ),
    ] = False,
) -> None:
    """Get detailed information about a user.

    Supports:
    - User ID: U0123456789
    - Username: @john.doe
    - Email: john@example.com (or @john@example.com)

    Examples:
        slack users get U0123456789
        slack users get @john.doe
        slack users get john@example.com
        slack users get @john.doe --json
    """
    ctx = get_context()
    slack = ctx.get_slack_client()

    try:
        # Check if the user_ref looks like a raw user ID
        if user_ref.startswith("U") and len(user_ref) > 1 and user_ref[1:].replace("-", "").isalnum():
            # Direct user ID lookup
            user = get_user(slack, user_ref)
            if user is None:
                error_console.print(f"[red]User not found: {user_ref}[/red]")
                raise typer.Exit(1) from None
        else:
            # Try to resolve @username or email
            result = resolve_user(slack, user_ref)
            if result is None:
                error_console.print(f"[red]User not found: {user_ref}[/red]")
                raise typer.Exit(1) from None

            user_id, _ = result
            user = get_user(slack, user_id)
            if user is None:
                error_console.print(f"[red]User not found: {user_ref}[/red]")
                raise typer.Exit(1) from None

        if output_json_flag:
            output_json(_user_to_dict(user))
        else:
            # Display detailed user info
            print(f"User ID:      {user.id}")
            print(f"Username:     @{user.name}")
            print(f"Display Name: {user.display_name}")
            print(f"Real Name:    {user.real_name}")
            print(f"Email:        {user.email or '(not available)'}")
            print(f"Is Bot:       {'Yes' if user.is_bot else 'No'}")
            print(f"Is Admin:     {'Yes' if user.is_admin else 'No'}")
            print(f"Deleted:      {'Yes' if user.deleted else 'No'}")

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")
        raise typer.Exit(1) from None
