"""Slack CLI client that encapsulates org configuration and WebClient."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .logging import get_logger
from .retry import create_web_client

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
        """Lazily create WebClient with retry handlers."""
        if self._client is None:
            self._client = create_web_client(token=self.token)
        return self._client

    def _check_response(self, response: dict, operation: str = "API call") -> dict:
        """Check if Slack API response is OK, raise SlackApiError if not.

        Args:
            response: The Slack API response dict.
            operation: Description of the operation for error messages.

        Returns:
            The response dict if OK.

        Raises:
            SlackApiError: If response["ok"] is False.
        """
        if not response.get("ok"):
            raise SlackApiError(
                f"{operation} failed: {response.get('error', 'unknown error')}",
                response,
            )
        return response

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

    def resolve_user(self, user_ref: str) -> tuple[str, str] | None:
        """Resolve a user reference to a user ID and name.

        Supports:
        - Raw user IDs: U0123456789
        - Username with @: @john.doe
        - Email with @: @john@example.com

        Args:
            user_ref: User reference - @username, @email, or raw user ID.

        Returns:
            Tuple of (user_id, username), or None if not found.
        """
        from .users import resolve_user

        return resolve_user(self, user_ref)

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
            self._check_response(response, "Fetch messages")

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
            self._check_response(response, "Fetch thread replies")

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
        self._check_response(response, "Send message")

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
        self._check_response(response, "Edit message")

        return {
            "ok": True,
            "channel": response.get("channel"),
            "ts": response.get("ts"),
            "text": response.get("text"),
            "message": response.get("message"),
        }

    def delete_message(
        self,
        channel_id: str,
        ts: str,
    ) -> dict[str, Any]:
        """Delete a message from a channel.

        Args:
            channel_id: The channel ID.
            ts: The timestamp of the message to delete.

        Returns:
            The API response data.

        Raises:
            SlackApiError: If the API call fails.
        """
        logger.debug(f"Deleting message {ts} from {channel_id}")
        response = self.client.chat_delete(
            channel=channel_id,
            ts=ts,
        )
        self._check_response(response, "Delete message")

        return {
            "ok": True,
            "channel": response.get("channel"),
            "ts": response.get("ts"),
        }

    # -------------------------------------------------------------------------
    # Reactions
    # -------------------------------------------------------------------------

    def add_reaction(
        self,
        channel_id: str,
        ts: str,
        emoji: str,
    ) -> dict[str, Any]:
        """Add a reaction to a message.

        Args:
            channel_id: The channel ID.
            ts: The timestamp of the message to react to.
            emoji: The emoji name (without colons).

        Returns:
            The API response data.

        Raises:
            SlackApiError: If the API call fails.
        """
        logger.debug(f"Adding reaction '{emoji}' to message {ts} in {channel_id}")
        response = self.client.reactions_add(
            channel=channel_id,
            timestamp=ts,
            name=emoji,
        )
        self._check_response(response, "Add reaction")

        return {
            "ok": True,
            "channel": channel_id,
            "ts": ts,
            "emoji": emoji,
        }

    def remove_reaction(
        self,
        channel_id: str,
        ts: str,
        emoji: str,
    ) -> dict[str, Any]:
        """Remove a reaction from a message.

        Args:
            channel_id: The channel ID.
            ts: The timestamp of the message to remove reaction from.
            emoji: The emoji name (without colons).

        Returns:
            The API response data.

        Raises:
            SlackApiError: If the API call fails.
        """
        logger.debug(f"Removing reaction '{emoji}' from message {ts} in {channel_id}")
        response = self.client.reactions_remove(
            channel=channel_id,
            timestamp=ts,
            name=emoji,
        )
        self._check_response(response, "Remove reaction")

        return {
            "ok": True,
            "channel": channel_id,
            "ts": ts,
            "emoji": emoji,
        }

    # -------------------------------------------------------------------------
    # Direct Messages
    # -------------------------------------------------------------------------

    def open_dm(self, user_id: str) -> dict[str, Any]:
        """Open a direct message conversation with a user.

        Args:
            user_id: The Slack user ID.

        Returns:
            The API response data including the DM channel info.

        Raises:
            SlackApiError: If the API call fails.
        """
        logger.debug(f"Opening DM conversation with user {user_id}")
        response = self.client.conversations_open(users=[user_id])
        self._check_response(response, "Open DM")

        return {
            "ok": True,
            "channel": response.get("channel"),
        }

    # -------------------------------------------------------------------------
    # Pins
    # -------------------------------------------------------------------------

    def pin_message(
        self,
        channel_id: str,
        ts: str,
    ) -> dict[str, Any]:
        """Pin a message to a channel.

        Args:
            channel_id: The channel ID.
            ts: The timestamp of the message to pin.

        Returns:
            The API response data.

        Raises:
            SlackApiError: If the API call fails.
        """
        logger.debug(f"Pinning message {ts} in {channel_id}")
        response = self.client.pins_add(
            channel=channel_id,
            timestamp=ts,
        )
        self._check_response(response, "Pin message")

        return {
            "ok": True,
            "channel": channel_id,
            "ts": ts,
        }

    def unpin_message(
        self,
        channel_id: str,
        ts: str,
    ) -> dict[str, Any]:
        """Unpin a message from a channel.

        Args:
            channel_id: The channel ID.
            ts: The timestamp of the message to unpin.

        Returns:
            The API response data.

        Raises:
            SlackApiError: If the API call fails.
        """
        logger.debug(f"Unpinning message {ts} from {channel_id}")
        response = self.client.pins_remove(
            channel=channel_id,
            timestamp=ts,
        )
        self._check_response(response, "Unpin message")

        return {
            "ok": True,
            "channel": channel_id,
            "ts": ts,
        }

    def list_pins(
        self,
        channel_id: str,
    ) -> dict[str, Any]:
        """List pinned messages in a channel.

        Args:
            channel_id: The channel ID.

        Returns:
            The API response data including pinned items.

        Raises:
            SlackApiError: If the API call fails.
        """
        logger.debug(f"Listing pinned messages in {channel_id}")
        response = self.client.pins_list(channel=channel_id)
        self._check_response(response, "List pins")

        return {
            "ok": True,
            "channel": channel_id,
            "items": response.get("items", []),
        }

    # -------------------------------------------------------------------------
    # Scheduled Messages
    # -------------------------------------------------------------------------

    def schedule_message(
        self,
        channel_id: str,
        text: str,
        post_at: int,
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        """Schedule a message for future delivery.

        Args:
            channel_id: The channel ID.
            text: The message text.
            post_at: Unix timestamp for when to send the message.
            thread_ts: Optional thread timestamp to reply to.

        Returns:
            The API response data including the scheduled_message_id.

        Raises:
            SlackApiError: If the API call fails.
        """
        kwargs: dict[str, Any] = {
            "channel": channel_id,
            "text": text,
            "post_at": post_at,
        }

        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        logger.debug(
            f"Scheduling message in {channel_id} for {post_at}" + (f" (thread: {thread_ts})" if thread_ts else "")
        )
        response = self.client.chat_scheduleMessage(**kwargs)
        self._check_response(response, "Schedule message")

        return {
            "ok": True,
            "channel": response.get("channel"),
            "scheduled_message_id": response.get("scheduled_message_id"),
            "post_at": response.get("post_at"),
            "message": response.get("message"),
        }

    def list_scheduled_messages(
        self,
        channel_id: str | None = None,
    ) -> dict[str, Any]:
        """List scheduled messages.

        Args:
            channel_id: Optional channel ID to filter by.

        Returns:
            The API response data including scheduled messages.

        Raises:
            SlackApiError: If the API call fails.
        """
        kwargs: dict[str, Any] = {}

        if channel_id:
            kwargs["channel"] = channel_id

        logger.debug("Listing scheduled messages" + (f" for {channel_id}" if channel_id else ""))
        response = self.client.chat_scheduledMessages_list(**kwargs)
        self._check_response(response, "List scheduled messages")

        return {
            "ok": True,
            "scheduled_messages": response.get("scheduled_messages", []),
        }

    def delete_scheduled_message(
        self,
        channel_id: str,
        scheduled_message_id: str,
    ) -> dict[str, Any]:
        """Delete a scheduled message.

        Args:
            channel_id: The channel ID.
            scheduled_message_id: The scheduled message ID to delete.

        Returns:
            The API response data.

        Raises:
            SlackApiError: If the API call fails.
        """
        logger.debug(f"Deleting scheduled message {scheduled_message_id} from {channel_id}")
        response = self.client.chat_deleteScheduledMessage(
            channel=channel_id,
            scheduled_message_id=scheduled_message_id,
        )
        self._check_response(response, "Delete scheduled message")

        return {
            "ok": True,
            "channel": channel_id,
            "scheduled_message_id": scheduled_message_id,
        }

    # -------------------------------------------------------------------------
    # File Upload
    # -------------------------------------------------------------------------

    def upload_file(
        self,
        file_path: str,
        channel_id: str | None = None,
        thread_ts: str | None = None,
        initial_comment: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Upload a file to Slack using the files.upload_v2 API.

        This uses the modern external upload flow:
        1. Get upload URL from files.getUploadURLExternal
        2. POST file content to the returned URL
        3. Complete upload with files.completeUploadExternal

        The SDK's files_upload_v2 method handles this flow automatically.

        Args:
            file_path: Path to the file to upload.
            channel_id: Optional channel ID to share the file to.
            thread_ts: Optional thread timestamp to share the file in.
            initial_comment: Optional message to include with the file.
            title: Optional title for the file (defaults to filename).

        Returns:
            The API response data including uploaded file info.

        Raises:
            SlackApiError: If the API call fails.
            FileNotFoundError: If the file doesn't exist.
        """
        import os
        from pathlib import Path

        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        if not path.is_file():
            raise ValueError(f"Path is not a file: {file_path}")

        # Prepare upload kwargs
        kwargs: dict[str, Any] = {
            "file": str(path),
            "filename": path.name,
        }

        if title:
            kwargs["title"] = title
        else:
            kwargs["title"] = path.name

        if channel_id:
            kwargs["channel"] = channel_id

        if thread_ts:
            kwargs["thread_ts"] = thread_ts

        if initial_comment:
            kwargs["initial_comment"] = initial_comment

        file_size = os.path.getsize(path)
        logger.debug(
            f"Uploading file {path.name} ({file_size} bytes)"
            + (f" to {channel_id}" if channel_id else "")
            + (f" (thread: {thread_ts})" if thread_ts else "")
        )

        response = self.client.files_upload_v2(**kwargs)
        self._check_response(response, "Upload file")

        # Extract file info from response
        # files_upload_v2 returns file info in 'file' or 'files' key
        file_info = response.get("file") or {}
        files = response.get("files", [])
        if files and not file_info:
            file_info = files[0] if files else {}

        return {
            "ok": True,
            "file": file_info,
        }

    def download_file(
        self,
        url: str,
        output_path: str,
    ) -> dict[str, Any]:
        """Download a file from Slack.

        Slack files require authentication via the token in the Authorization header.

        Args:
            url: The url_private_download URL of the file.
            output_path: Path where the file should be saved.

        Returns:
            Dictionary with download result info.

        Raises:
            SlackApiError: If the download fails.
        """
        import urllib.request
        from pathlib import Path

        logger.debug(f"Downloading file from {url} to {output_path}")

        # Create request with authorization header
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Bearer {self.token}")

        from .retry import create_ssl_context

        try:
            with urllib.request.urlopen(req, context=create_ssl_context()) as response:
                # Get filename from Content-Disposition header if available
                content_disposition = response.headers.get("Content-Disposition", "")
                suggested_name = None
                if "filename=" in content_disposition:
                    import re

                    match = re.search(r'filename="?([^";\r\n]+)"?', content_disposition)
                    if match:
                        suggested_name = match.group(1)

                # Create output directory if it doesn't exist
                output = Path(output_path)
                output.parent.mkdir(parents=True, exist_ok=True)

                # Download and write the file
                content = response.read()
                output.write_bytes(content)

                return {
                    "ok": True,
                    "path": str(output),
                    "size": len(content),
                    "suggested_name": suggested_name,
                }

        except urllib.error.HTTPError as e:
            raise SlackApiError(f"Download failed: HTTP {e.code} {e.reason}", {"error": str(e)}) from e
        except urllib.error.URLError as e:
            raise SlackApiError(f"Download failed: {e.reason}", {"error": str(e)}) from e

    def get_file_info(self, file_id: str) -> dict[str, Any]:
        """Get information about a file.

        Args:
            file_id: The Slack file ID.

        Returns:
            The file info from the API.

        Raises:
            SlackApiError: If the API call fails.
        """
        logger.debug(f"Getting file info for {file_id}")
        response = self.client.files_info(file=file_id)
        self._check_response(response, "Get file info")

        return {
            "ok": True,
            "file": response.get("file", {}),
        }

    # -------------------------------------------------------------------------
    # Search
    # -------------------------------------------------------------------------

    def search_messages(
        self,
        query: str,
        sort: str = "score",
        sort_dir: str = "desc",
        count: int = 20,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search for messages in Slack.

        Args:
            query: The search query string (supports Slack search modifiers).
            sort: Sort by 'score' or 'timestamp'.
            sort_dir: Sort direction, 'asc' or 'desc'.
            count: Number of results per page (max 100).
            page: Page number (1-indexed).

        Returns:
            The search results from the API.

        Raises:
            SlackApiError: If the API call fails.
        """
        logger.debug(f"Searching messages: {query} (sort={sort}, sort_dir={sort_dir}, count={count}, page={page})")
        response = self.client.search_messages(
            query=query,
            sort=sort,
            sort_dir=sort_dir,
            count=count,
            page=page,
        )
        self._check_response(response, "Search messages")

        return {
            "ok": True,
            "query": response.get("query", query),
            "messages": response.get("messages", {}),
        }

    def search_files(
        self,
        query: str,
        sort: str = "score",
        sort_dir: str = "desc",
        count: int = 20,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search for files in Slack.

        Args:
            query: The search query string (supports Slack search modifiers).
            sort: Sort by 'score' or 'timestamp'.
            sort_dir: Sort direction, 'asc' or 'desc'.
            count: Number of results per page (max 100).
            page: Page number (1-indexed).

        Returns:
            The search results from the API.

        Raises:
            SlackApiError: If the API call fails.
        """
        logger.debug(f"Searching files: {query} (sort={sort}, sort_dir={sort_dir}, count={count}, page={page})")
        response = self.client.search_files(
            query=query,
            sort=sort,
            sort_dir=sort_dir,
            count=count,
            page=page,
        )
        self._check_response(response, "Search files")

        return {
            "ok": True,
            "query": response.get("query", query),
            "files": response.get("files", {}),
        }
