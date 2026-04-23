"""Slack CLI client that encapsulates org configuration and WebClient."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

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
        *,
        direction: Literal["head", "tail"],
        count: int,
        oldest: datetime | None = None,
        latest: datetime | None = None,
        after_ts: str | None = None,
        before_ts: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool, bool]:
        """Fetch messages from a channel with peek-ahead has_more detection.

        Args:
            channel_id: The channel ID.
            direction: "head" walks forward from the lower bound, "tail" walks
                backward from the upper bound.
            count: Maximum number of messages to return (excluding peek-ahead).
            oldest: Lower bound window (inclusive as Slack interprets it).
            latest: Upper bound window.
            after_ts: Exclusive lower cursor. Combined with ``oldest`` by
                picking whichever is newer.
            before_ts: Exclusive upper cursor. Combined with ``latest`` by
                picking whichever is older.

        Returns:
            Tuple ``(messages_asc, has_more_before, has_more_after)``.
            ``messages_asc`` is sorted ascending by ``ts``.
        """

        def _ts_to_float(value: str) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        # Translate cursor + window to Slack API oldest/latest.
        # "oldest" on the API is a lower bound; "latest" is an upper bound.
        oldest_values: list[float] = []
        if oldest is not None:
            oldest_values.append(oldest.timestamp())
        if after_ts is not None:
            oldest_values.append(_ts_to_float(after_ts))
        api_oldest = max(oldest_values) if oldest_values else None

        latest_values: list[float] = []
        if latest is not None:
            latest_values.append(latest.timestamp())
        if before_ts is not None:
            latest_values.append(_ts_to_float(before_ts))
        api_latest = min(latest_values) if latest_values else None

        peek_target = count + 1  # peek ahead by one
        messages: list[dict[str, Any]] = []
        cursor: str | None = None
        api_has_more_after_fetch = False

        base_kwargs: dict[str, Any] = {
            "channel": channel_id,
            "inclusive": False,
        }
        if api_oldest is not None:
            base_kwargs["oldest"] = f"{api_oldest:.6f}"
        if api_latest is not None:
            base_kwargs["latest"] = f"{api_latest:.6f}"

        while len(messages) < peek_target:
            kwargs = dict(base_kwargs)
            kwargs["limit"] = min(peek_target - len(messages), 1000)
            if cursor:
                kwargs["cursor"] = cursor

            logger.debug(f"Fetching messages (cursor: {cursor or 'initial'})")
            response = self.client.conversations_history(**kwargs)
            self._check_response(response, "Fetch messages")

            batch = response.get("messages", [])
            messages.extend(batch)

            api_has_more_after_fetch = bool(response.get("has_more", False))
            if not api_has_more_after_fetch:
                break

            response_metadata = response.get("response_metadata", {})
            cursor = response_metadata.get("next_cursor")
            if not cursor:
                break

        # Sort ascending by ts so direction-specific trimming is unambiguous.
        messages.sort(key=lambda m: _ts_to_float(m.get("ts", "0")))

        # Decide which peek-ahead side applies. If we got more than `count`,
        # there's at least one extra item. Drop it from the slice we return
        # but remember that there are more on that side.
        overflow = len(messages) > count
        has_more_before = False
        has_more_after = False

        if direction == "tail":
            # We keep the newest `count` messages; older side may have more.
            if overflow:
                messages = messages[-count:]
                has_more_before = True
            # If the API said has_more while we were still fetching toward
            # the lower bound and we haven't filled the slice, we also
            # trust the API signal.
            elif api_has_more_after_fetch:
                has_more_before = True
        else:  # head
            # Keep the oldest `count` messages; newer side may have more.
            if overflow:
                messages = messages[:count]
                has_more_after = True
            elif api_has_more_after_fetch:
                has_more_after = True

        return messages, has_more_before, has_more_after

    def get_thread_replies(
        self,
        channel_id: str,
        thread_ts: str,
        *,
        direction: Literal["head", "tail"],
        count: int,
        after_ts: str | None = None,
        before_ts: str | None = None,
    ) -> tuple[list[dict[str, Any]], bool, bool]:
        """Fetch thread replies with peek-ahead has_more detection.

        The Slack ``conversations.replies`` API returns the parent first,
        followed by replies in chronological order. Since it has no
        direction control the way ``conversations.history`` does, we fetch
        the full reply list and apply direction / count / cursors in Python.

        Args:
            channel_id: The channel ID.
            thread_ts: The thread parent timestamp.
            direction: "head" (first replies) or "tail" (last replies).
            count: Maximum number of replies to return.
            after_ts: Exclusive lower cursor applied to replies.
            before_ts: Exclusive upper cursor applied to replies.

        Returns:
            Tuple ``(messages_asc, has_more_before, has_more_after)`` where
            the first element of ``messages_asc`` is always the parent
            followed by the selected slice of replies in ascending ts order.
        """

        def _ts_to_float(value: str) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        all_messages: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            kwargs: dict[str, Any] = {
                "channel": channel_id,
                "ts": thread_ts,
                "limit": 1000,
            }
            if cursor:
                kwargs["cursor"] = cursor

            logger.debug(f"Fetching thread replies (cursor: {cursor or 'initial'})")
            response = self.client.conversations_replies(**kwargs)
            self._check_response(response, "Fetch thread replies")

            batch = response.get("messages", [])
            all_messages.extend(batch)

            if not response.get("has_more", False):
                break

            response_metadata = response.get("response_metadata", {})
            cursor = response_metadata.get("next_cursor")
            if not cursor:
                break

        if not all_messages:
            return [], False, False

        # Parent is the first message whose ts matches thread_ts.
        parent: dict[str, Any] | None = None
        replies: list[dict[str, Any]] = []
        for msg in all_messages:
            if parent is None and msg.get("ts") == thread_ts:
                parent = msg
            else:
                replies.append(msg)
        if parent is None:
            # Fall back: treat the first message as parent.
            parent = all_messages[0]
            replies = all_messages[1:]

        replies.sort(key=lambda m: _ts_to_float(m.get("ts", "0")))

        # Apply cursors to the reply list exclusively.
        after_f = _ts_to_float(after_ts) if after_ts else None
        before_f = _ts_to_float(before_ts) if before_ts else None

        filtered: list[dict[str, Any]] = []
        trimmed_before_cursor = False
        trimmed_after_cursor = False
        for reply in replies:
            ts_f = _ts_to_float(reply.get("ts", "0"))
            if after_f is not None and ts_f <= after_f:
                trimmed_before_cursor = True
                continue
            if before_f is not None and ts_f >= before_f:
                trimmed_after_cursor = True
                continue
            filtered.append(reply)

        has_more_before = trimmed_before_cursor
        has_more_after = trimmed_after_cursor

        if direction == "tail":
            if len(filtered) > count:
                filtered = filtered[-count:]
                has_more_before = True
        else:  # head
            if len(filtered) > count:
                filtered = filtered[:count]
                has_more_after = True

        return [parent, *filtered], has_more_before, has_more_after

    def fetch_full_thread(
        self,
        channel_id: str,
        thread_ts: str,
    ) -> list[dict[str, Any]]:
        """Fetch the full thread (parent + all replies) in ascending order.

        Used by ``--with-threads`` where the page-size flags constrain the
        top-level parents but each parent is expanded in full.
        """

        def _ts_to_float(value: str) -> float:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        all_messages: list[dict[str, Any]] = []
        cursor: str | None = None

        while True:
            kwargs: dict[str, Any] = {
                "channel": channel_id,
                "ts": thread_ts,
                "limit": 1000,
            }
            if cursor:
                kwargs["cursor"] = cursor

            response = self.client.conversations_replies(**kwargs)
            self._check_response(response, "Fetch thread replies")

            all_messages.extend(response.get("messages", []))

            if not response.get("has_more", False):
                break
            cursor = response.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        all_messages.sort(key=lambda m: _ts_to_float(m.get("ts", "0")))
        return all_messages

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
