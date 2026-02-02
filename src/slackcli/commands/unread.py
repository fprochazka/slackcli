"""Unread command for Slack CLI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

import typer
from slack_sdk.errors import SlackApiError

from ..context import get_context
from ..logging import console, error_console, get_logger
from ..output import output_json

if TYPE_CHECKING:
    from ..client import SlackCli

logger = get_logger(__name__)


@dataclass
class UnreadChannel:
    """Represents a channel with unread messages."""

    id: str
    name: str
    unread_count: int
    unread_count_display: int
    is_private: bool
    is_im: bool
    is_mpim: bool

    def get_display_name(self, users: dict[str, str]) -> str:
        """Get display name for the channel.

        Args:
            users: Dictionary mapping user ID to display name.

        Returns:
            Display name with appropriate prefix.
        """
        if self.is_im:
            # DMs show as the user's name
            # The name field contains the user ID for DMs
            user_id = self.name.replace("DM:", "") if self.name.startswith("DM:") else self.name
            user_name = users.get(user_id, user_id)
            return f"@{user_name}"
        if self.is_mpim:
            # Group DMs - name is typically "mpdm-user1--user2--user3-1"
            return f"(group) {self.name}"
        if self.is_private:
            return f"#{self.name} (private)"
        return f"#{self.name}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "unread_count": self.unread_count,
            "unread_count_display": self.unread_count_display,
            "is_private": self.is_private,
            "is_im": self.is_im,
            "is_mpim": self.is_mpim,
        }


def fetch_unread_channels(slack: SlackCli) -> list[UnreadChannel]:
    """Fetch channels with unread messages from Slack API.

    Args:
        slack: The SlackCli client.

    Returns:
        List of channels with unread messages, sorted by unread count descending.
    """
    unread_channels: list[UnreadChannel] = []
    cursor: str | None = None

    # Include all conversation types
    types = "public_channel,private_channel,mpim,im"

    while True:
        try:
            logger.debug(f"Fetching conversations page (cursor: {cursor or 'initial'})")
            response = slack.client.conversations_list(
                types=types,
                limit=1000,
                cursor=cursor,
                exclude_archived=True,
            )

            if not response["ok"]:
                raise SlackApiError(f"API error: {response.get('error', 'unknown')}", response)

            channels = response.get("channels", [])
            for channel_data in channels:
                # Check for unread messages
                unread_count = channel_data.get("unread_count", 0)
                unread_count_display = channel_data.get("unread_count_display", 0)

                # Only include channels with unread messages
                if unread_count > 0 or unread_count_display > 0:
                    # For IMs, the name is the user ID
                    name = channel_data.get("name", "")
                    is_im = channel_data.get("is_im", False)
                    if is_im:
                        user_id = channel_data.get("user", "")
                        name = user_id if user_id else name

                    unread_channels.append(
                        UnreadChannel(
                            id=channel_data.get("id", ""),
                            name=name,
                            unread_count=unread_count,
                            unread_count_display=unread_count_display,
                            is_private=channel_data.get("is_private", False),
                            is_im=is_im,
                            is_mpim=channel_data.get("is_mpim", False),
                        )
                    )

            # Check for more pages
            response_metadata = response.get("response_metadata", {})
            cursor = response_metadata.get("next_cursor")

            if not cursor:
                break

        except SlackApiError as e:
            error_console.print(f"[red]Slack API error: {e.response.get('error', str(e))}[/red]")
            raise typer.Exit(1) from None

    # Sort by unread count descending (use unread_count_display as primary, fall back to unread_count)
    unread_channels.sort(
        key=lambda c: (c.unread_count_display or c.unread_count, c.unread_count),
        reverse=True,
    )

    logger.debug(f"Found {len(unread_channels)} channels with unread messages")
    return unread_channels


def unread_command(
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output as JSON.",
        ),
    ] = False,
) -> None:
    """Show channels with unread messages.

    Lists all channels, DMs, and group DMs that have unread messages,
    sorted by the number of unread messages (most unread first).
    """
    ctx = get_context()
    slack = ctx.get_slack_client()

    console.print("[dim]Fetching unread channels from Slack API...[/dim]")
    unread_channels = fetch_unread_channels(slack)

    if not unread_channels:
        if json_output:
            output_json({"channels": []})
        else:
            console.print("[dim]No unread messages.[/dim]")
        return

    # Collect user IDs for DM name resolution
    user_ids_to_fetch: set[str] = set()
    for channel in unread_channels:
        if channel.is_im:
            user_ids_to_fetch.add(channel.name)

    # Resolve user names
    users: dict[str, str] = {}
    if user_ids_to_fetch:
        users = slack.get_user_display_names(list(user_ids_to_fetch))

    if json_output:
        # Add resolved user names to the output
        channels_data = []
        for channel in unread_channels:
            data = channel.to_dict()
            data["display_name"] = channel.get_display_name(users)
            channels_data.append(data)
        output_json({"channels": channels_data})
    else:
        # Calculate column widths for alignment
        max_name_len = max(len(channel.get_display_name(users)) for channel in unread_channels)

        for channel in unread_channels:
            display_name = channel.get_display_name(users)
            # Use unread_count_display if available (respects muted channels), else unread_count
            count = channel.unread_count_display or channel.unread_count
            print(f"{display_name:<{max_name_len}}  {count:>3} unread")

        console.print(f"\n[dim]Total: {len(unread_channels)} channels with unread messages[/dim]")
