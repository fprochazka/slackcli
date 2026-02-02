"""Slack CLI client that encapsulates org configuration and WebClient."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .logging import get_logger

if TYPE_CHECKING:
    from .commands.conversations import ConversationLoadResult
    from .models import Conversation
    from .users import UserInfo

logger = get_logger(__name__)


@dataclass
class SlackCli:
    """Main Slack CLI client that holds config and WebClient."""

    org_name: str
    token: str
    _client: WebClient | None = field(default=None, repr=False)

    @property
    def client(self) -> WebClient:
        """Lazily create WebClient."""
        if self._client is None:
            self._client = WebClient(token=self.token)
        return self._client

    # -------------------------------------------------------------------------
    # Conversations
    # -------------------------------------------------------------------------

    def get_conversations(self, fresh: bool = False) -> "ConversationLoadResult":
        """Load conversations with caching.

        Args:
            fresh: If True, force refresh from API regardless of cache state.

        Returns:
            ConversationLoadResult with conversations and cache metadata.
        """
        from .commands.conversations import load_conversations

        return load_conversations(self, fresh=fresh)

    def get_conversations_from_cache(self) -> list["Conversation"] | None:
        """Load conversations from cache only (no API call).

        Returns:
            List of conversations, or None if cache doesn't exist.
        """
        from .commands.conversations import load_conversations_from_cache

        return load_conversations_from_cache(self.org_name)

    # -------------------------------------------------------------------------
    # Users
    # -------------------------------------------------------------------------

    def get_user(self, user_id: str, fresh: bool = False) -> "UserInfo | None":
        """Get user info with caching.

        Args:
            user_id: The Slack user ID.
            fresh: If True, force refresh from API regardless of cache state.

        Returns:
            The UserInfo, or None if user could not be found.
        """
        from .users import get_user

        return get_user(self, user_id, fresh=fresh)

    def get_users(self, user_ids: list[str]) -> dict[str, "UserInfo"]:
        """Get multiple users, fetching from API if not cached or expired.

        Args:
            user_ids: List of Slack user IDs.

        Returns:
            Dictionary mapping user ID to UserInfo for found users.
        """
        from .users import get_users

        return get_users(self, user_ids)

    def get_user_display_names(self, user_ids: list[str]) -> dict[str, str]:
        """Get display names for multiple users.

        Args:
            user_ids: List of Slack user IDs.

        Returns:
            Dictionary mapping user ID to username (or display name fallback).
        """
        from .users import get_user_display_names

        return get_user_display_names(self, user_ids)

    def get_channel_names(self) -> dict[str, str]:
        """Get channel names from the conversations cache.

        Returns:
            Dictionary mapping channel ID to channel name.
        """
        from .users import get_channel_names

        return get_channel_names(self)

    # -------------------------------------------------------------------------
    # Messages
    # -------------------------------------------------------------------------

    def get_messages(
        self,
        channel_id: str,
        oldest: datetime | None = None,
        latest: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch messages from a channel.

        Args:
            channel_id: The channel ID.
            oldest: Oldest message time (inclusive).
            latest: Latest message time (inclusive).
            limit: Maximum number of messages to fetch.

        Returns:
            List of message data from API.
        """
        messages: list[dict[str, Any]] = []
        cursor: str | None = None

        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "limit": min(limit, 1000),  # API max is 1000
        }

        if oldest:
            kwargs["oldest"] = f"{oldest.timestamp():.6f}"
        if latest:
            kwargs["latest"] = f"{latest.timestamp():.6f}"

        while len(messages) < limit:
            if cursor:
                kwargs["cursor"] = cursor

            logger.debug(f"Fetching messages (cursor: {cursor or 'initial'})")
            response = self.client.conversations_history(**kwargs)

            if not response["ok"]:
                raise SlackApiError(f"API error: {response.get('error', 'unknown')}", response)

            batch = response.get("messages", [])
            messages.extend(batch)

            # Check for more pages
            if not response.get("has_more", False):
                break

            response_metadata = response.get("response_metadata", {})
            cursor = response_metadata.get("next_cursor")
            if not cursor:
                break

            # Adjust limit for next request
            remaining = limit - len(messages)
            kwargs["limit"] = min(remaining, 1000)

        # Trim to exact limit
        return messages[:limit]

    def get_thread_replies(
        self,
        channel_id: str,
        thread_ts: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch thread replies.

        Args:
            channel_id: The channel ID.
            thread_ts: The thread timestamp.
            limit: Maximum number of messages to fetch.

        Returns:
            List of message data from API (parent first, then replies).
        """
        messages: list[dict[str, Any]] = []
        cursor: str | None = None

        while len(messages) < limit:
            kwargs: dict[str, Any] = {
                "channel": channel_id,
                "ts": thread_ts,
                "limit": min(limit - len(messages), 1000),
            }
            if cursor:
                kwargs["cursor"] = cursor

            logger.debug(f"Fetching thread replies (cursor: {cursor or 'initial'})")
            response = self.client.conversations_replies(**kwargs)

            if not response["ok"]:
                raise SlackApiError(f"API error: {response.get('error', 'unknown')}", response)

            batch = response.get("messages", [])
            messages.extend(batch)

            # Check for more pages
            if not response.get("has_more", False):
                break

            response_metadata = response.get("response_metadata", {})
            cursor = response_metadata.get("next_cursor")
            if not cursor:
                break

        return messages[:limit]

    def get_message(
        self,
        channel_id: str,
        message_ts: str,
    ) -> dict[str, Any] | None:
        """Fetch a single message from a channel.

        Args:
            channel_id: The channel ID.
            message_ts: The message timestamp.

        Returns:
            Message data or None if not found.
        """
        response = self.client.conversations_history(
            channel=channel_id,
            latest=message_ts,
            inclusive=True,
            limit=1,
        )

        if response["ok"]:
            messages = response.get("messages", [])
            if messages:
                return messages[0]

        return None

    def get_thread_reply(
        self,
        channel_id: str,
        thread_ts: str,
        message_ts: str,
    ) -> dict[str, Any] | None:
        """Fetch a specific reply from a thread.

        Args:
            channel_id: The channel ID.
            thread_ts: The parent thread timestamp.
            message_ts: The specific reply timestamp.

        Returns:
            Message data or None if not found.
        """
        response = self.client.conversations_replies(
            channel=channel_id,
            ts=thread_ts,
            latest=message_ts,
            inclusive=True,
            limit=1,
        )

        if response["ok"]:
            messages = response.get("messages", [])
            # Find the exact message by ts
            for msg in messages:
                if msg.get("ts") == message_ts:
                    return msg

        return None

    # -------------------------------------------------------------------------
    # Send Messages
    # -------------------------------------------------------------------------

    def send_message(
        self,
        channel_id: str,
        text: str,
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        """Send a message to a channel.

        Args:
            channel_id: The channel ID.
            text: The message text.
            thread_ts: Optional thread timestamp to reply to.

        Returns:
            The API response data including the message timestamp.

        Raises:
            SlackApiError: If the API call fails.
        """
        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "text": text,
        }

        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        logger.debug(f"Sending message to {channel_id}" + (f" (thread: {thread_ts})" if thread_ts else ""))
        response = self.client.chat_postMessage(**kwargs)

        if not response["ok"]:
            raise SlackApiError(f"API error: {response.get('error', 'unknown')}", response)

        return {
            "ok": True,
            "channel": response.get("channel"),
            "ts": response.get("ts"),
            "message": response.get("message"),
        }

    def edit_message(
        self,
        channel_id: str,
        ts: str,
        text: str,
    ) -> dict[str, Any]:
        """Edit an existing message in a channel.

        Args:
            channel_id: The channel ID.
            ts: The timestamp of the message to edit.
            text: The new message text.

        Returns:
            The API response data including the updated message.

        Raises:
            SlackApiError: If the API call fails.
        """
        logger.debug(f"Editing message {ts} in {channel_id}")
        response = self.client.chat_update(
            channel=channel_id,
            ts=ts,
            text=text,
        )

        if not response["ok"]:
            raise SlackApiError(f"API error: {response.get('error', 'unknown')}", response)

        return {
            "ok": True,
            "channel": response.get("channel"),
            "ts": response.get("ts"),
            "text": response.get("text"),
            "message": response.get("message"),
        }
