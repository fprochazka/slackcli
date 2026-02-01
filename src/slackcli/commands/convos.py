"""Conversations command group for Slack CLI."""

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..cache import get_cache_age, load_cache, save_cache
from ..context import get_context
from ..logging import error_console, get_logger

app = typer.Typer(
    name="convos",
    help="Manage Slack conversations (channels, DMs, groups).",
    no_args_is_help=True,
)

console = Console()
logger = get_logger(__name__)

CACHE_NAME = "conversations"


@dataclass
class Conversation:
    """Represents a Slack conversation."""

    id: str
    name: str
    is_private: bool
    is_channel: bool
    is_group: bool
    is_im: bool
    is_mpim: bool
    topic: str
    purpose: str
    num_members: int
    created: int  # Unix timestamp

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Conversation":
        """Create a Conversation from Slack API response data."""
        # Handle different conversation types
        # For IMs, the name is the user ID
        name = data.get("name", "")
        if not name and data.get("is_im"):
            name = f"DM:{data.get('user', 'unknown')}"
        if not name and data.get("is_mpim"):
            name = data.get("name", "Group DM")

        return cls(
            id=data.get("id", ""),
            name=name,
            is_private=data.get("is_private", False),
            is_channel=data.get("is_channel", False),
            is_group=data.get("is_group", False),
            is_im=data.get("is_im", False),
            is_mpim=data.get("is_mpim", False),
            topic=data.get("topic", {}).get("value", "") if isinstance(data.get("topic"), dict) else "",
            purpose=data.get("purpose", {}).get("value", "") if isinstance(data.get("purpose"), dict) else "",
            num_members=data.get("num_members", 0),
            created=data.get("created", 0),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for caching."""
        return {
            "id": self.id,
            "name": self.name,
            "is_private": self.is_private,
            "is_channel": self.is_channel,
            "is_group": self.is_group,
            "is_im": self.is_im,
            "is_mpim": self.is_mpim,
            "topic": self.topic,
            "purpose": self.purpose,
            "num_members": self.num_members,
            "created": self.created,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Conversation":
        """Create from cached dictionary."""
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            is_private=data.get("is_private", False),
            is_channel=data.get("is_channel", False),
            is_group=data.get("is_group", False),
            is_im=data.get("is_im", False),
            is_mpim=data.get("is_mpim", False),
            topic=data.get("topic", ""),
            purpose=data.get("purpose", ""),
            num_members=data.get("num_members", 0),
            created=data.get("created", 0),
        )

    def get_type(self) -> str:
        """Get a human-readable type string."""
        if self.is_im:
            return "DM"
        if self.is_mpim:
            return "Group DM"
        if self.is_channel:
            if self.is_private:
                return "Private Channel"
            return "Public Channel"
        if self.is_group:
            return "Group"
        return "Unknown"


def fetch_all_conversations(client: WebClient) -> list[Conversation]:
    """Fetch all conversations from Slack API with pagination.

    Args:
        client: The Slack WebClient.

    Returns:
        List of all conversations.
    """
    conversations: list[Conversation] = []
    cursor: str | None = None

    # Include all conversation types
    types = "public_channel,private_channel,mpim,im"

    while True:
        try:
            logger.debug(f"Fetching conversations page (cursor: {cursor or 'initial'})")
            response = client.conversations_list(
                types=types,
                limit=1000,
                cursor=cursor,
                exclude_archived=False,
            )

            if not response["ok"]:
                raise SlackApiError(f"API error: {response.get('error', 'unknown')}", response)

            channels = response.get("channels", [])
            for channel_data in channels:
                conversations.append(Conversation.from_api(channel_data))

            # Check for more pages
            response_metadata = response.get("response_metadata", {})
            cursor = response_metadata.get("next_cursor")

            if not cursor:
                break

        except SlackApiError as e:
            error_console.print(f"[red]Slack API error: {e.response.get('error', str(e))}[/red]")
            raise typer.Exit(1) from None

    logger.debug(f"Fetched {len(conversations)} conversations total")
    return conversations


def load_conversations_from_cache(org_name: str) -> list[Conversation] | None:
    """Load conversations from cache.

    Args:
        org_name: The organization name.

    Returns:
        List of conversations, or None if cache doesn't exist.
    """
    cache_data = load_cache(org_name, CACHE_NAME)

    if cache_data is None:
        return None

    data = cache_data.get("data", {})
    conversations_data = data.get("conversations", [])

    return [Conversation.from_dict(c) for c in conversations_data]


def save_conversations_to_cache(org_name: str, conversations: list[Conversation]) -> None:
    """Save conversations to cache.

    Args:
        org_name: The organization name.
        conversations: List of conversations to cache.
    """
    data = {
        "conversations": [c.to_dict() for c in conversations],
    }
    cache_path = save_cache(org_name, CACHE_NAME, data)
    logger.debug(f"Saved {len(conversations)} conversations to {cache_path}")


def display_conversations(conversations: list[Conversation]) -> None:
    """Display conversations in a table.

    Args:
        conversations: List of conversations to display.
    """
    table = Table(title="Conversations")

    table.add_column("ID", style="dim")
    table.add_column("Name", style="cyan")
    table.add_column("Type", style="green")
    table.add_column("Members", justify="right")
    table.add_column("Private", justify="center")
    table.add_column("Created", style="dim")

    # Sort by type and name
    sorted_convos = sorted(
        conversations,
        key=lambda c: (
            0 if c.is_channel and not c.is_private else 1 if c.is_channel else 2 if c.is_group else 3,
            c.name.lower(),
        ),
    )

    for convo in sorted_convos:
        created_str = ""
        if convo.created:
            created_str = datetime.fromtimestamp(convo.created).strftime("%Y-%m-%d")

        table.add_row(
            convo.id,
            convo.name or "(no name)",
            convo.get_type(),
            str(convo.num_members) if convo.num_members else "-",
            "Yes" if convo.is_private else "No",
            created_str,
        )

    console.print(table)
    console.print(f"\n[dim]Total: {len(conversations)} conversations[/dim]")


@app.command("list")
def list_conversations(
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh",
            "-r",
            help="Force refresh the cache from Slack API.",
        ),
    ] = False,
) -> None:
    """List all Slack conversations (channels, DMs, groups)."""
    ctx = get_context()
    org = ctx.get_org()
    org_name = org.name

    conversations: list[Conversation] | None = None

    # Try to load from cache unless refresh is requested
    if not refresh:
        conversations = load_conversations_from_cache(org_name)
        if conversations is not None:
            cache_age = get_cache_age(org_name, CACHE_NAME)
            if cache_age:
                console.print(f"[dim]Using cached data from {cache_age.strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
                console.print("[dim]Use --refresh to update from Slack API[/dim]\n")

    # Fetch from API if no cache or refresh requested
    if conversations is None:
        console.print("[dim]Fetching conversations from Slack API...[/dim]")
        client = WebClient(token=org.token)
        conversations = fetch_all_conversations(client)
        save_conversations_to_cache(org_name, conversations)
        console.print("[green]Cache updated successfully[/green]\n")

    display_conversations(conversations)
