"""Data models for Slack CLI.

This module contains dataclasses that represent Slack entities.
These models serve as the single source of truth for data representation
and can be serialized to both JSON and human-readable text output.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Type alias for message text extraction and mention resolution functions
MessageTextFunc = Callable[[dict[str, Any], dict[str, str], dict[str, str]], str]


@dataclass
class FileAttachment:
    """Represents a file attached to a Slack message."""

    id: str
    name: str
    title: str
    mimetype: str
    filetype: str
    size: int
    url_private: str
    url_private_download: str
    permalink: str

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> FileAttachment:
        """Create a FileAttachment from Slack API response data.

        Args:
            data: The file data from Slack API.

        Returns:
            A FileAttachment instance.
        """
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            title=data.get("title", ""),
            mimetype=data.get("mimetype", ""),
            filetype=data.get("filetype", ""),
            size=data.get("size", 0),
            url_private=data.get("url_private", ""),
            url_private_download=data.get("url_private_download", ""),
            permalink=data.get("permalink", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "title": self.title,
            "mimetype": self.mimetype,
            "filetype": self.filetype,
            "size": self.size,
            "url_private": self.url_private,
            "url_private_download": self.url_private_download,
            "permalink": self.permalink,
        }

    def format_size(self) -> str:
        """Format the file size for human display."""
        if self.size < 1024:
            return f"{self.size} B"
        elif self.size < 1024 * 1024:
            return f"{self.size / 1024:.1f} KB"
        elif self.size < 1024 * 1024 * 1024:
            return f"{self.size / (1024 * 1024):.1f} MB"
        else:
            return f"{self.size / (1024 * 1024 * 1024):.1f} GB"


@dataclass
class Reaction:
    """Represents a reaction on a Slack message."""

    name: str
    count: int
    user_names: list[str] = field(default_factory=list)

    @classmethod
    def from_api(
        cls,
        data: dict[str, Any],
        users: dict[str, str],
    ) -> Reaction:
        """Create a Reaction from Slack API response data.

        Args:
            data: The reaction data from Slack API.
            users: Dictionary mapping user ID to display name.

        Returns:
            A Reaction instance.
        """
        user_ids = data.get("users", [])
        user_names = [users.get(uid, uid) for uid in user_ids]
        return cls(
            name=data.get("name", ""),
            count=data.get("count", 0),
            user_names=user_names,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "count": self.count,
            "users": self.user_names,
        }


@dataclass
class Message:
    """Represents a Slack message with resolved user/channel references."""

    ts: str
    user_id: str | None
    user_name: str | None
    text: str
    thread_ts: str | None
    reply_count: int
    reactions: list[Reaction] = field(default_factory=list)
    replies: list[Message] = field(default_factory=list)
    files: list[FileAttachment] = field(default_factory=list)

    @property
    def datetime(self) -> datetime | None:
        """Get the message timestamp as a datetime object."""
        try:
            return datetime.fromtimestamp(float(self.ts), tz=timezone.utc)
        except (ValueError, OSError):
            return None

    @property
    def datetime_str(self) -> str:
        """Get the message timestamp as a formatted string."""
        dt = self.datetime
        if dt is None:
            return self.ts
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    @classmethod
    def from_api(
        cls,
        data: dict[str, Any],
        users: dict[str, str],
        channels: dict[str, str],
        get_text_func: MessageTextFunc,
        resolve_mentions_func: MessageTextFunc,
    ) -> Message:
        """Create a Message from Slack API response data.

        Args:
            data: The message data from Slack API.
            users: Dictionary mapping user ID to display name.
            channels: Dictionary mapping channel ID to channel name.
            get_text_func: Function to extract text from message (handles blocks).
            resolve_mentions_func: Function to resolve Slack mentions in text.

        Returns:
            A Message instance.
        """
        ts = data.get("ts", "")
        user_id = data.get("user", "")
        thread_ts = data.get("thread_ts")

        # Get message text
        text = get_text_func(data, users, channels)
        text = resolve_mentions_func(text, users, channels)

        # Get username
        user_name = users.get(user_id, user_id) if user_id else None

        # Parse reactions
        reactions_data = data.get("reactions", [])
        reactions = [Reaction.from_api(r, users) for r in reactions_data]

        # Parse inline thread replies if present
        replies_data = data.get("replies", [])
        replies = [cls.from_api(r, users, channels, get_text_func, resolve_mentions_func) for r in replies_data]

        # Parse file attachments
        files_data = data.get("files", [])
        files = [FileAttachment.from_api(f) for f in files_data]

        return cls(
            ts=ts,
            user_id=user_id or None,
            user_name=user_name,
            text=text,
            thread_ts=thread_ts if thread_ts != ts else None,
            reply_count=data.get("reply_count", 0),
            reactions=reactions,
            replies=replies,
            files=files,
        )

    def to_dict(self, include_replies: bool = True) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization.

        Args:
            include_replies: Whether to include inline thread replies.

        Returns:
            Dictionary suitable for JSON serialization.
        """
        result: dict[str, Any] = {
            "ts": self.ts,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "text": self.text,
            "thread_ts": self.thread_ts,
            "reply_count": self.reply_count,
            "reactions": [r.to_dict() for r in self.reactions],
            "files": [f.to_dict() for f in self.files],
        }

        if include_replies and self.replies:
            result["replies"] = [r.to_dict(include_replies=False) for r in self.replies]

        return result


@dataclass
class MessagesOutput:
    """Output container for a list of messages from a channel."""

    channel_id: str
    channel_name: str
    messages: list[Message]

    def to_dict(self, include_replies: bool = True) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization.

        Args:
            include_replies: Whether to include inline thread replies.

        Returns:
            Dictionary suitable for JSON serialization.
        """
        return {
            "channel": self.channel_id,
            "channel_name": self.channel_name,
            "messages": [m.to_dict(include_replies=include_replies) for m in self.messages],
        }


@dataclass
class ResolvedMessage:
    """Output container for a resolved single message (from URL)."""

    channel_id: str
    channel_name: str
    message_ts: str
    thread_ts: str | None
    is_thread_reply: bool
    message: Message

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "channel_id": self.channel_id,
            "channel_name": self.channel_name,
            "message_ts": self.message_ts,
            "thread_ts": self.thread_ts,
            "is_thread_reply": self.is_thread_reply,
            "message": self.message.to_dict(include_replies=False),
        }


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
    def from_api(cls, data: dict[str, Any]) -> Conversation:
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
        """Convert to dictionary for caching/JSON serialization."""
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
    def from_dict(cls, data: dict[str, Any]) -> Conversation:
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


def resolve_slack_mentions(text: str, users: dict[str, str], channels: dict[str, str]) -> str:
    """Replace Slack mention macros with readable names.

    Handles:
    - <@U08GTCPJW95> - user mentions, replaced with @username
    - <#C01234567> or <#C01234567|channel-name> - channel mentions, replaced with #channel-name
    - <https://example.com|link text> - links, replaced with the URL
    - <!subteam^S123|@team-name> - user group mentions, replaced with @team-name

    Args:
        text: The original message text with Slack formatting.
        users: Dictionary mapping user ID to username.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Text with mentions replaced with readable names.
    """
    if not text:
        return text

    # Replace user mentions: <@U08GTCPJW95> or <@U08GTCPJW95|display_name>
    def replace_user_mention(match: re.Match) -> str:
        user_id = match.group(1)
        username = users.get(user_id, user_id)
        return f"@{username}"

    text = re.sub(r"<@([A-Z0-9]+)(?:\|[^>]*)?>", replace_user_mention, text)

    # Replace channel mentions: <#C01234567> or <#C01234567|channel-name>
    def replace_channel_mention(match: re.Match) -> str:
        channel_id = match.group(1)
        # If the mention includes a name, use it
        channel_name_in_mention = match.group(2)
        if channel_name_in_mention:
            return f"#{channel_name_in_mention}"
        # Otherwise look up from cache
        channel_name = channels.get(channel_id, channel_id)
        return f"#{channel_name}"

    text = re.sub(r"<#([A-Z0-9]+)(?:\|([^>]*))?>", replace_channel_mention, text)

    # Replace links: <https://example.com|link text> or <https://example.com>
    def replace_link(match: re.Match) -> str:
        url = match.group(1)
        link_text = match.group(2)
        if link_text:
            return f"{link_text} ({url})"
        return url

    text = re.sub(r"<(https?://[^|>]+)(?:\|([^>]*))?>", replace_link, text)

    # Replace user group mentions: <!subteam^S123|@team-name> or <!subteam^S123>
    def replace_subteam(match: re.Match) -> str:
        team_name = match.group(2)
        if team_name:
            return team_name
        return f"@subteam-{match.group(1)}"

    text = re.sub(r"<!subteam\^([A-Z0-9]+)(?:\|([^>]*))?>", replace_subteam, text)

    # Replace special mentions: <!here>, <!channel>, <!everyone>
    text = re.sub(r"<!here>", "@here", text)
    text = re.sub(r"<!channel>", "@channel", text)
    text = re.sub(r"<!everyone>", "@everyone", text)

    return text
