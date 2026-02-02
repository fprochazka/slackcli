"""User cache management for Slack CLI.

This module provides per-user file caching with lazy loading and soft expiry.
User info is stored in individual files at ~/.cache/slackcli/<org>/users/<userId>.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from slack_sdk.errors import SlackApiError

from .cache import get_cache_dir
from .logging import get_logger

if TYPE_CHECKING:
    from .client import SlackCli

logger = get_logger(__name__)

# Cache expiry time in hours (soft expiry - still use if expired, but refresh inline)
USER_CACHE_EXPIRY_HOURS = 24


@dataclass
class UserInfo:
    """Represents cached Slack user information."""

    id: str
    name: str
    real_name: str
    display_name: str
    email: str | None
    is_bot: bool
    is_admin: bool
    deleted: bool
    updated_at: str  # ISO format datetime

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> UserInfo:
        """Create a UserInfo from Slack API response data.

        Args:
            data: The user data from Slack API.

        Returns:
            A UserInfo instance.
        """
        profile = data.get("profile", {})
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            real_name=data.get("real_name", "") or profile.get("real_name", ""),
            display_name=profile.get("display_name", "") or profile.get("real_name", "") or data.get("name", ""),
            email=profile.get("email"),
            is_bot=data.get("is_bot", False),
            is_admin=data.get("is_admin", False),
            deleted=data.get("deleted", False),
            updated_at=datetime.now().isoformat(),
        )

    def to_cache_dict(self) -> dict[str, Any]:
        """Convert to dictionary for caching with metadata.

        Returns:
            Dictionary suitable for JSON serialization.
        """
        return {
            "_meta": {
                "updated_at": self.updated_at,
                "version": 1,
            },
            "id": self.id,
            "name": self.name,
            "real_name": self.real_name,
            "display_name": self.display_name,
            "email": self.email,
            "is_bot": self.is_bot,
            "is_admin": self.is_admin,
            "deleted": self.deleted,
        }

    @classmethod
    def from_cache_dict(cls, data: dict[str, Any]) -> UserInfo:
        """Create from cached dictionary.

        Args:
            data: The cached data dictionary.

        Returns:
            A UserInfo instance.
        """
        meta = data.get("_meta", {})
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            real_name=data.get("real_name", ""),
            display_name=data.get("display_name", ""),
            email=data.get("email"),
            is_bot=data.get("is_bot", False),
            is_admin=data.get("is_admin", False),
            deleted=data.get("deleted", False),
            updated_at=meta.get("updated_at", ""),
        )

    def get_best_display_name(self) -> str:
        """Get the best display name for the user.

        Prefers display_name, falls back to real_name, then name, then id.

        Returns:
            The best available display name.
        """
        return self.display_name or self.real_name or self.name or self.id

    def get_username(self) -> str:
        """Get the username for the user.

        Prefers name (the @username), falls back to display_name, then real_name, then id.

        Returns:
            The username or best available fallback.
        """
        return self.name or self.display_name or self.real_name or self.id

    def is_expired(self) -> bool:
        """Check if the cached user info is expired.

        Returns:
            True if older than USER_CACHE_EXPIRY_HOURS.
        """
        if not self.updated_at:
            return True
        try:
            updated = datetime.fromisoformat(self.updated_at)
            return datetime.now() - updated > timedelta(hours=USER_CACHE_EXPIRY_HOURS)
        except ValueError:
            return True


def get_users_cache_dir(org_name: str) -> Path:
    """Get the users cache directory for an organization.

    Args:
        org_name: The organization name.

    Returns:
        Path to the users cache directory.
    """
    return get_cache_dir(org_name) / "users"


def ensure_users_cache_dir(org_name: str) -> Path:
    """Ensure the users cache directory exists.

    Args:
        org_name: The organization name.

    Returns:
        Path to the users cache directory.
    """
    cache_dir = get_users_cache_dir(org_name)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_user_cache_path(org_name: str, user_id: str) -> Path:
    """Get the path to a specific user's cache file.

    Args:
        org_name: The organization name.
        user_id: The Slack user ID.

    Returns:
        Path to the user's cache file.
    """
    return get_users_cache_dir(org_name) / f"{user_id}.json"


def load_user_from_cache(org_name: str, user_id: str) -> UserInfo | None:
    """Load a user from cache.

    Args:
        org_name: The organization name.
        user_id: The Slack user ID.

    Returns:
        The cached UserInfo, or None if not found or invalid.
    """
    cache_path = get_user_cache_path(org_name, user_id)

    if not cache_path.exists():
        return None

    try:
        with open(cache_path) as f:
            data = json.load(f)
            return UserInfo.from_cache_dict(data)
    except (json.JSONDecodeError, OSError) as e:
        logger.debug(f"Failed to load user cache for {user_id}: {e}")
        return None


def save_user_to_cache(org_name: str, user: UserInfo) -> Path:
    """Save a user to cache.

    Args:
        org_name: The organization name.
        user: The UserInfo to cache.

    Returns:
        Path to the saved cache file.
    """
    ensure_users_cache_dir(org_name)
    cache_path = get_user_cache_path(org_name, user.id)

    with open(cache_path, "w") as f:
        json.dump(user.to_cache_dict(), f, indent=2)

    return cache_path


def fetch_user_from_api(slack: SlackCli, user_id: str) -> UserInfo | None:
    """Fetch a user from the Slack API.

    Args:
        slack: The SlackCli client.
        user_id: The Slack user ID.

    Returns:
        The UserInfo, or None if not found.
    """
    try:
        response = slack.client.users_info(user=user_id)
        if response["ok"]:
            return UserInfo.from_api(response.get("user", {}))
    except SlackApiError as e:
        logger.debug(f"Failed to fetch user {user_id}: {e}")
    return None


def get_user(slack: SlackCli, user_id: str, fresh: bool = False) -> UserInfo | None:
    """Get user info, using cache unless fresh=True or cache expired.

    Uses lazy loading with soft expiry (24 hours):
    - If cached and not expired and fresh=False, return cached version
    - If cached but expired, or fresh=True, fetch fresh and update cache
    - If not cached, fetch and cache

    Args:
        slack: The SlackCli client.
        user_id: The Slack user ID.
        fresh: If True, force refresh from API regardless of cache state.

    Returns:
        The UserInfo, or None if user could not be found.
    """
    # Try to load from cache (unless forcing fresh)
    cached_user = None if fresh else load_user_from_cache(slack.org_name, user_id)

    if cached_user is not None:
        if not cached_user.is_expired():
            logger.debug(f"Using cached user info for {user_id}")
            return cached_user

        # Cache is expired, fetch fresh
        logger.debug(f"Cache expired for user {user_id}, fetching fresh")

    # Fetch from API
    user = fetch_user_from_api(slack, user_id)

    if user is not None:
        save_user_to_cache(slack.org_name, user)
        return user

    # If API fetch failed but we have expired cache, use it as fallback
    if cached_user is not None:
        logger.debug(f"API fetch failed for {user_id}, using expired cache")
        return cached_user

    return None


def get_users(slack: SlackCli, user_ids: list[str]) -> dict[str, UserInfo]:
    """Get multiple users, fetching from API if not cached or expired.

    Args:
        slack: The SlackCli client.
        user_ids: List of Slack user IDs.

    Returns:
        Dictionary mapping user ID to UserInfo for found users.
    """
    result: dict[str, UserInfo] = {}

    for user_id in user_ids:
        if not user_id:
            continue
        user = get_user(slack, user_id)
        if user is not None:
            result[user_id] = user

    return result


def get_user_display_names(slack: SlackCli, user_ids: list[str]) -> dict[str, str]:
    """Get display names for multiple users.

    Convenience function that returns a simple dict of user_id -> display_name.
    Prefers the `name` field (Slack username) over display names.

    Args:
        slack: The SlackCli client.
        user_ids: List of Slack user IDs.

    Returns:
        Dictionary mapping user ID to username (or display name fallback).
    """
    users = get_users(slack, user_ids)
    return {user_id: user.get_username() for user_id, user in users.items()}


def get_channel_names(slack: SlackCli) -> dict[str, str]:
    """Get channel names from the conversations cache.

    Args:
        slack: The SlackCli client.

    Returns:
        Dictionary mapping channel ID to channel name.
    """
    conversations = slack.get_conversations_from_cache()
    if conversations is None:
        return {}

    return {convo.id: convo.name or "" for convo in conversations if convo.id}
