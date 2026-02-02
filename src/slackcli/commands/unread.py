"""Unread command for Slack CLI."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any

import typer
from rich.progress import Progress, SpinnerColumn, TextColumn
from slack_sdk.errors import SlackApiError

from ..context import get_context
from ..errors import format_error_with_hint
from ..logging import console, error_console, get_logger
from ..output import output_json

if TYPE_CHECKING:
    from ..client import SlackCli

logger = get_logger(__name__)

# Maximum number of concurrent API calls to avoid rate limiting
MAX_CONCURRENT_REQUESTS = 10

# Default limit for conversations to check (0 = no limit)
DEFAULT_CONVERSATION_LIMIT = 100


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


def _get_user_conversations(slack: SlackCli) -> list[dict[str, Any]]:
    """Get all conversations the user is a member of.

    Args:
        slack: The SlackCli client.

    Returns:
        List of basic conversation data from users_conversations API.
    """
    conversations: list[dict[str, Any]] = []
    cursor: str | None = None
    types = "public_channel,private_channel,mpim,im"

    while True:
        logger.debug(f"Fetching user conversations page (cursor: {cursor or 'initial'})")
        response = slack.client.users_conversations(
            types=types,
            limit=200,
            cursor=cursor,
            exclude_archived=True,
        )

        if not response["ok"]:
            raise SlackApiError(f"API error: {response.get('error', 'unknown')}", response)

        conversations.extend(response.get("channels", []))

        response_metadata = response.get("response_metadata", {})
        cursor = response_metadata.get("next_cursor")
        if not cursor:
            break

    return conversations


def _get_unread_count_for_channel(slack: SlackCli, channel_id: str, is_im: bool, is_mpim: bool) -> tuple[int, int]:
    """Get unread count for a specific channel.

    For DMs and MPIMs, conversations.info returns unread_count directly.
    For channels, we need to use last_read and count messages after it.

    Args:
        slack: The SlackCli client.
        channel_id: The channel ID.
        is_im: Whether this is a direct message.
        is_mpim: Whether this is a multi-person direct message.

    Returns:
        Tuple of (unread_count, unread_count_display).
    """
    # Get detailed channel info
    info_response = slack.client.conversations_info(channel=channel_id)
    if not info_response["ok"]:
        return 0, 0

    channel_info = info_response.get("channel", {})

    # For DMs and MPIMs, unread_count is returned directly
    if is_im or is_mpim:
        unread_count = channel_info.get("unread_count", 0) or 0
        unread_count_display = channel_info.get("unread_count_display", 0) or 0
        return unread_count, unread_count_display

    # For channels, we need to count messages after last_read
    last_read = channel_info.get("last_read")
    if not last_read or last_read == "0000000000.000000":
        # Never read - get total message count (limited)
        history = slack.client.conversations_history(channel=channel_id, limit=100)
        if history["ok"]:
            messages = history.get("messages", [])
            count = len(messages)
            # If there are more messages, indicate 100+
            if history.get("has_more", False):
                count = 100
            return count, count
        return 0, 0

    # Get messages after last_read
    history = slack.client.conversations_history(channel=channel_id, oldest=last_read, limit=100)
    if not history["ok"]:
        return 0, 0

    messages = history.get("messages", [])
    # Filter out the message at last_read itself (it's been read)
    unread = [m for m in messages if m.get("ts") != last_read]
    count = len(unread)

    # If there are more, indicate 100+
    if history.get("has_more", False):
        count = 100

    return count, count


def _check_conversation_for_unread(slack: SlackCli, conv: dict[str, Any]) -> UnreadChannel | None:
    """Check a single conversation for unread messages.

    Args:
        slack: The SlackCli client.
        conv: The conversation data from users_conversations.

    Returns:
        UnreadChannel if there are unread messages, None otherwise.
    """
    channel_id = conv.get("id", "")
    is_im = conv.get("is_im", False)
    is_mpim = conv.get("is_mpim", False)

    try:
        unread_count, unread_count_display = _get_unread_count_for_channel(slack, channel_id, is_im, is_mpim)
    except SlackApiError as e:
        logger.debug(f"Error getting unread count for {channel_id}: {e}")
        return None

    if unread_count > 0 or unread_count_display > 0:
        # For IMs, the name is the user ID
        name = conv.get("name", "")
        if is_im:
            user_id = conv.get("user", "")
            name = user_id if user_id else name

        return UnreadChannel(
            id=channel_id,
            name=name,
            unread_count=unread_count,
            unread_count_display=unread_count_display,
            is_private=conv.get("is_private", False),
            is_im=is_im,
            is_mpim=is_mpim,
        )

    return None


def fetch_unread_channels(
    slack: SlackCli,
    show_progress: bool = True,
    limit: int = DEFAULT_CONVERSATION_LIMIT,
) -> list[UnreadChannel]:
    """Fetch channels with unread messages from Slack API.

    Note: The Slack API does not return unread counts in conversations.list.
    We need to call conversations.info for each conversation to get unread info.
    For DMs/MPIMs, this returns unread_count directly.
    For channels, we use last_read and conversations.history to count unread messages.

    Uses concurrent requests to speed up the process.

    Args:
        slack: The SlackCli client.
        show_progress: Whether to show a progress indicator.
        limit: Maximum number of conversations to check (0 = no limit).
            Conversations are sorted by recent activity before limiting.

    Returns:
        List of channels with unread messages, sorted by unread count descending.
    """
    unread_channels: list[UnreadChannel] = []

    try:
        # Get all conversations the user is a member of
        conversations = _get_user_conversations(slack)

        # Sort by 'updated' timestamp (most recent first) to prioritize active conversations
        conversations.sort(key=lambda c: c.get("updated", 0) or 0, reverse=True)

        # Apply limit if specified
        if limit > 0 and len(conversations) > limit:
            logger.debug(f"Limiting from {len(conversations)} to {limit} most recently active conversations")
            conversations = conversations[:limit]

        total = len(conversations)
        logger.debug(f"Checking {total} conversations for unread messages")

        if total == 0:
            return []

        # Use ThreadPoolExecutor for concurrent API calls
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
            disable=not show_progress,
        ) as progress:
            task = progress.add_task(f"Checking {total} conversations...", total=total)

            with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_REQUESTS) as executor:
                # Submit all tasks
                future_to_conv = {
                    executor.submit(_check_conversation_for_unread, slack, conv): conv for conv in conversations
                }

                # Collect results as they complete
                for future in as_completed(future_to_conv):
                    progress.advance(task)
                    result = future.result()
                    if result is not None:
                        unread_channels.append(result)

    except SlackApiError as e:
        error_msg, hint = format_error_with_hint(e)
        error_console.print(f"[red]{error_msg}[/red]")
        if hint:
            error_console.print(f"[dim]Hint: {hint}[/dim]")
        raise typer.Exit(1) from None

    # Sort by unread count descending
    unread_channels.sort(
        key=lambda c: (c.unread_count_display or c.unread_count, c.unread_count),
        reverse=True,
    )

    logger.debug(f"Found {len(unread_channels)} channels with unread messages")
    return unread_channels


def unread_command(
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            "-n",
            help=(
                "Maximum number of conversations to check. "
                "Conversations are sorted by recent activity. "
                "Use 0 to check all conversations (may be slow)."
            ),
        ),
    ] = DEFAULT_CONVERSATION_LIMIT,
    output_json_flag: Annotated[
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

    By default, checks the 100 most recently active conversations.
    Use --limit 0 to check all conversations (may take a while for large workspaces).
    """
    ctx = get_context()
    slack = ctx.get_slack_client()

    unread_channels = fetch_unread_channels(slack, show_progress=not output_json_flag, limit=limit)

    if not unread_channels:
        if output_json_flag:
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

    if output_json_flag:
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
