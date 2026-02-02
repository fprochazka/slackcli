"""Conversations command group for Slack CLI."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Annotated

import typer
from slack_sdk.errors import SlackApiError

from ..cache import get_cache_age, load_cache, save_cache
from ..context import get_context
from ..errors import format_error_with_hint
from ..logging import console, error_console, get_logger
from ..models import Conversation
from ..output import output_conversations_text

if TYPE_CHECKING:
    from ..client import SlackCli

app = typer.Typer(
    name="conversations",
    help="Manage Slack conversations (channels, DMs, groups).",
    no_args_is_help=True,
    rich_markup_mode=None,
)

logger = get_logger(__name__)

CACHE_NAME = "conversations"
CACHE_MAX_AGE_HOURS = 6


@dataclass
class ConversationLoadResult:
    """Result of loading conversations with metadata about cache status."""

    conversations: list[Conversation]
    from_cache: bool
    cache_age: datetime | None = None
    refreshed: bool = False


def fetch_mpim_members(slack: SlackCli, conversation_id: str) -> list[str]:
    """Fetch member IDs for a group DM (mpim).

    Args:
        slack: The SlackCli client.
        conversation_id: The conversation ID.

    Returns:
        List of member user IDs.
    """
    try:
        response = slack.client.conversations_members(channel=conversation_id, limit=100)
        if response["ok"]:
            return response.get("members", [])
    except SlackApiError as e:
        logger.debug(f"Failed to fetch members for {conversation_id}: {e}")
    return []


def fetch_all_conversations(slack: SlackCli) -> list[Conversation]:
    """Fetch all conversations from Slack API with pagination.

    Args:
        slack: The SlackCli client.

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
            response = slack.client.conversations_list(
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
            error_msg, hint = format_error_with_hint(e)
            error_console.print(f"[red]{error_msg}[/red]")
            if hint:
                error_console.print(f"[dim]Hint: {hint}[/dim]")
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
            member_ids = fetch_mpim_members(slack, convo.id)
            convo.member_ids = member_ids
            user_ids_to_fetch.update(member_ids)

    # Fetch user info (uses new per-user file caching with lazy loading)
    if user_ids_to_fetch:
        console.print(f"[dim]Resolving {len(user_ids_to_fetch)} user names...[/dim]")
        # This will fetch and cache users individually with 24h soft expiry
        slack.get_user_display_names(list(user_ids_to_fetch))

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


def load_conversations(
    slack: SlackCli,
    fresh: bool = False,
) -> ConversationLoadResult:
    """Load conversations, using cache unless fresh=True or cache expired.

    The function handles all cache loading/saving internally:
    - Check if cache exists and is not expired (6 hours)
    - If cache valid and fresh=False, return cached data
    - If cache expired or fresh=True, fetch from API and update cache

    Args:
        slack: The SlackCli client.
        fresh: If True, force refresh from API regardless of cache state.

    Returns:
        ConversationLoadResult with conversations and cache metadata.
    """
    needs_refresh = fresh

    # Check if cache is expired (older than 6 hours)
    if not needs_refresh and is_cache_expired(slack.org_name, CACHE_NAME):
        console.print("[dim]Cache is older than 6 hours, refreshing...[/dim]")
        needs_refresh = True

    # Try to load from cache unless refresh is needed
    if not needs_refresh:
        conversations = load_conversations_from_cache(slack.org_name)
        if conversations is not None:
            cache_age = get_cache_age(slack.org_name, CACHE_NAME)
            if cache_age:
                console.print(
                    f"[dim]Using cached conversations (updated {cache_age.strftime('%Y-%m-%d %H:%M:%S')})[/dim]"
                )
                console.print("[dim]Use --refresh to update from Slack API[/dim]\n")
            return ConversationLoadResult(
                conversations=conversations,
                from_cache=True,
                cache_age=cache_age,
                refreshed=False,
            )

    # Fetch from API
    console.print("[dim]Fetching conversations from Slack API...[/dim]")
    conversations = fetch_all_conversations(slack)
    save_conversations_to_cache(slack.org_name, conversations)
    console.print("[green]Cache updated successfully[/green]\n")

    return ConversationLoadResult(
        conversations=conversations,
        from_cache=False,
        cache_age=datetime.now(),
        refreshed=True,
    )


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
    slack = ctx.get_slack_client()

    # Load conversations using the centralized function (handles caching internally)
    result = slack.get_conversations(fresh=refresh)
    conversations = result.conversations

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
    users = slack.get_user_display_names(list(user_ids_to_fetch))

    output_conversations_text(filtered_conversations, users)
