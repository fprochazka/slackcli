"""Conversations command group for Slack CLI."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any

import typer
from rich.console import Console
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..cache import get_cache_age, load_cache, save_cache
from ..context import get_context
from ..logging import error_console, get_logger
from ..users import get_user_display_names

app = typer.Typer(
    name="conversations",
    help="Manage Slack conversations (channels, DMs, groups).",
    no_args_is_help=True,
    rich_markup_mode=None,
)

console = Console()
logger = get_logger(__name__)

CACHE_NAME = "conversations"
CACHE_MAX_AGE_HOURS = 6


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
    is_member: bool
    topic: str
    purpose: str
    num_members: int
    created: int  # Unix timestamp
    user_id: str | None = None  # For DMs, the other user's ID
    member_ids: list[str] | None = None  # For group DMs, list of member IDs

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Conversation":
        """Create a Conversation from Slack API response data."""
        # Handle different conversation types
        # For IMs, the name is the user ID
        name = data.get("name", "")
        user_id = None
        if data.get("is_im"):
            user_id = data.get("user")
            if not name:
                name = f"DM:{user_id or 'unknown'}"
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
            is_member=data.get("is_member", False),
            topic=data.get("topic", {}).get("value", "") if isinstance(data.get("topic"), dict) else "",
            purpose=data.get("purpose", {}).get("value", "") if isinstance(data.get("purpose"), dict) else "",
            num_members=data.get("num_members", 0),
            created=data.get("created", 0),
            user_id=user_id,
            member_ids=None,  # Will be populated separately for mpim
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
            "is_member": self.is_member,
            "topic": self.topic,
            "purpose": self.purpose,
            "num_members": self.num_members,
            "created": self.created,
            "user_id": self.user_id,
            "member_ids": self.member_ids,
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
            is_member=data.get("is_member", False),
            topic=data.get("topic", ""),
            purpose=data.get("purpose", ""),
            num_members=data.get("num_members", 0),
            created=data.get("created", 0),
            user_id=data.get("user_id"),
            member_ids=data.get("member_ids"),
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


def fetch_mpim_members(client: WebClient, conversation_id: str) -> list[str]:
    """Fetch member IDs for a group DM (mpim).

    Args:
        client: The Slack WebClient.
        conversation_id: The conversation ID.

    Returns:
        List of member user IDs.
    """
    try:
        response = client.conversations_members(channel=conversation_id, limit=100)
        if response["ok"]:
            return response.get("members", [])
    except SlackApiError as e:
        logger.debug(f"Failed to fetch members for {conversation_id}: {e}")
    return []


def fetch_all_conversations(client: WebClient, org_name: str) -> list[Conversation]:
    """Fetch all conversations from Slack API with pagination.

    Args:
        client: The Slack WebClient.
        org_name: The organization name for caching users.

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

    # Collect user IDs that need to be resolved
    user_ids_to_fetch: set[str] = set()

    # Get user IDs from DMs
    for convo in conversations:
        if convo.is_im and convo.user_id:
            user_ids_to_fetch.add(convo.user_id)

    # Fetch members for group DMs (mpim)
    mpim_convos = [c for c in conversations if c.is_mpim]
    if mpim_convos:
        console.print(f"[dim]Fetching members for {len(mpim_convos)} group DMs...[/dim]")
        for convo in mpim_convos:
            member_ids = fetch_mpim_members(client, convo.id)
            convo.member_ids = member_ids
            user_ids_to_fetch.update(member_ids)

    # Fetch user info (uses new per-user file caching with lazy loading)
    if user_ids_to_fetch:
        console.print(f"[dim]Resolving {len(user_ids_to_fetch)} user names...[/dim]")
        # This will fetch and cache users individually with 24h soft expiry
        get_user_display_names(client, org_name, list(user_ids_to_fetch))

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


def get_display_name(convo: Conversation, users: dict[str, str]) -> str:
    """Get the display name for a conversation.

    Args:
        convo: The conversation.
        users: Dictionary mapping user ID to display name.

    Returns:
        Human-readable display name.
    """
    if convo.is_im and convo.user_id:
        return users.get(convo.user_id, convo.user_id)
    if convo.is_mpim and convo.member_ids:
        # Sort member names alphabetically
        member_names = sorted(users.get(uid, uid) for uid in convo.member_ids)
        return ", ".join(member_names)
    return convo.name or "(no name)"


def display_conversations(conversations: list[Conversation], users: dict[str, str]) -> None:
    """Display conversations in a simple list format.

    Args:
        conversations: List of conversations to display.
        users: Dictionary mapping user ID to display name.
    """
    # Sort by type and name
    sorted_convos = sorted(
        conversations,
        key=lambda c: (
            0 if c.is_channel and not c.is_private else 1 if c.is_channel else 2 if c.is_group else 3,
            get_display_name(c, users).lower(),
        ),
    )

    for convo in sorted_convos:
        display_name = get_display_name(convo, users)
        convo_type = convo.get_type()
        print(f"{convo.id}: {display_name} ({convo_type})")

    console.print(f"\n[dim]Total: {len(conversations)} conversations[/dim]")


def is_cache_expired(org_name: str, cache_name: str) -> bool:
    """Check if the cache is older than CACHE_MAX_AGE_HOURS.

    Args:
        org_name: The organization name.
        cache_name: The name of the cache.

    Returns:
        True if cache is expired or doesn't exist.
    """
    cache_age = get_cache_age(org_name, cache_name)
    if cache_age is None:
        return True
    return datetime.now() - cache_age > timedelta(hours=CACHE_MAX_AGE_HOURS)


def filter_conversations(
    conversations: list[Conversation],
    dms: bool = False,
    private: bool = False,
    public: bool = False,
    member: bool = False,
    non_member: bool = False,
) -> list[Conversation]:
    """Filter conversations based on flags.

    Args:
        conversations: List of conversations to filter.
        dms: Show only DMs and group DMs.
        private: Show only private channels.
        public: Show only public channels.
        member: Show only channels where user is a member.
        non_member: Show only channels where user is NOT a member.

    Returns:
        Filtered list of conversations.
    """
    result = conversations

    # Apply type filters (OR logic)
    type_filters_active = dms or private or public
    if type_filters_active:
        filtered = []
        for convo in result:
            is_dm_match = dms and (convo.is_im or convo.is_mpim)
            is_private_match = private and convo.is_channel and convo.is_private
            is_public_match = public and convo.is_channel and not convo.is_private
            if is_dm_match or is_private_match or is_public_match:
                filtered.append(convo)
        result = filtered

    # Apply membership filters (AND logic with type filters)
    # Note: DMs (is_im) and group DMs (is_mpim) are always considered "member" conversations
    # since you're always a member of your own DMs
    if member:
        result = [c for c in result if c.is_member or c.is_im or c.is_mpim]
    if non_member:
        # DMs/MPIMs are never "non-member" since you're always a member
        result = [c for c in result if not c.is_member and not c.is_im and not c.is_mpim]

    return result


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
    dms: Annotated[
        bool,
        typer.Option(
            "--dms",
            help="Show only DMs and group DMs.",
        ),
    ] = False,
    private: Annotated[
        bool,
        typer.Option(
            "--private",
            help="Show only private channels.",
        ),
    ] = False,
    public: Annotated[
        bool,
        typer.Option(
            "--public",
            help="Show only public channels.",
        ),
    ] = False,
    member: Annotated[
        bool,
        typer.Option(
            "--member",
            help="Show only channels where you are a member.",
        ),
    ] = False,
    non_member: Annotated[
        bool,
        typer.Option(
            "--non-member",
            help="Show only channels where you are NOT a member.",
        ),
    ] = False,
) -> None:
    """List all Slack conversations (channels, DMs, groups)."""
    ctx = get_context()
    org = ctx.get_org()
    org_name = org.name

    conversations: list[Conversation] | None = None
    needs_refresh = refresh

    # Check if cache is expired (older than 6 hours)
    if not needs_refresh and is_cache_expired(org_name, CACHE_NAME):
        console.print("[dim]Cache is older than 6 hours, refreshing...[/dim]")
        needs_refresh = True

    # Try to load from cache unless refresh is requested
    if not needs_refresh:
        conversations = load_conversations_from_cache(org_name)
        if conversations is not None:
            cache_age = get_cache_age(org_name, CACHE_NAME)
            if cache_age:
                console.print(f"[dim]Using cached data from {cache_age.strftime('%Y-%m-%d %H:%M:%S')}[/dim]")
                console.print("[dim]Use --refresh to update from Slack API[/dim]\n")

    # Fetch from API if no cache or refresh requested
    client = WebClient(token=org.token) if conversations is None else None
    if conversations is None:
        console.print("[dim]Fetching conversations from Slack API...[/dim]")
        conversations = fetch_all_conversations(client, org_name)
        save_conversations_to_cache(org_name, conversations)
        console.print("[green]Cache updated successfully[/green]\n")

    # Apply filters
    filtered_conversations = filter_conversations(
        conversations,
        dms=dms,
        private=private,
        public=public,
        member=member,
        non_member=non_member,
    )

    # Collect user IDs needed for display
    user_ids_to_fetch: set[str] = set()
    for convo in filtered_conversations:
        if convo.is_im and convo.user_id:
            user_ids_to_fetch.add(convo.user_id)
        if convo.is_mpim and convo.member_ids:
            user_ids_to_fetch.update(convo.member_ids)

    # Get user display names (uses per-user file caching with 24h soft expiry)
    if client is None:
        client = WebClient(token=org.token)
    users = get_user_display_names(client, org_name, list(user_ids_to_fetch))

    display_conversations(filtered_conversations, users)
