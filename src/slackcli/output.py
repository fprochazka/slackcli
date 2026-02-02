"""Unified output formatting for Slack CLI.

This module provides consistent output formatting for both JSON and text modes.
All output should go through these functions to ensure consistency.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .logging import console

if TYPE_CHECKING:
    from .models import Conversation, FileAttachment, Message, MessagesOutput, ResolvedMessage


def output_json(data: dict) -> None:
    """Output data as JSON.

    Uses plain print() to avoid Rich console formatting.

    Args:
        data: Dictionary to output as JSON.
    """
    print(json.dumps(data, indent=2, ensure_ascii=False))


def format_user_name(user_name: str | None, user_id: str | None = None) -> str:
    """Format a username for display.

    Args:
        user_name: The display name or username.
        user_id: Fallback user ID if name not available.

    Returns:
        Formatted username with @ prefix.
    """
    name = user_name or user_id or "(unknown)"
    if name and not name.startswith("@"):
        name = f"@{name}"
    return name


def format_message_text(text: str, indent: str = "  ") -> str:
    """Format message text with indentation.

    Args:
        text: The message text.
        indent: Indentation string.

    Returns:
        Formatted text with each line indented.
    """
    if not text:
        return f"{indent}(no text)"
    lines = text.split("\n")
    return "\n".join(f"{indent}{line}" for line in lines)


def format_reactions(
    reactions: list,
    mode: str,
) -> str:
    """Format reactions for text display.

    Args:
        reactions: List of Reaction objects.
        mode: 'off', 'counts', or 'names'.

    Returns:
        Formatted reactions string.
    """
    if mode == "off" or not reactions:
        return ""

    parts = []
    for reaction in reactions:
        if mode == "counts":
            parts.append(f":{reaction.name}: {reaction.count}")
        elif mode == "names":
            parts.append(f":{reaction.name}: {', '.join(reaction.user_names)}")

    return " ".join(parts)


def format_files(files: list[FileAttachment], indent: str = "  ") -> str:
    """Format file attachments for text display.

    Args:
        files: List of FileAttachment objects.
        indent: Indentation string.

    Returns:
        Formatted files string.
    """
    if not files:
        return ""

    lines = []
    for f in files:
        size_str = f.format_size()
        lines.append(f"{indent}[file: {f.name} ({size_str})]")
        lines.append(f"{indent}  download: {f.url_private_download}")
    return "\n".join(lines)


def output_messages_json(output: MessagesOutput, with_threads: bool = False) -> None:
    """Output messages as JSON.

    Args:
        output: The MessagesOutput to serialize.
        with_threads: Whether to include inline thread replies.
    """
    output_json(output.to_dict(include_replies=with_threads))


def output_messages_text(
    output: MessagesOutput,
    reactions_mode: str = "off",
    with_threads: bool = False,
) -> None:
    """Output messages as formatted text.

    Args:
        output: The MessagesOutput to display.
        reactions_mode: How to display reactions ('off', 'counts', 'names').
        with_threads: Whether to display inline thread replies.
    """
    for msg in output.messages:
        _output_message_text(msg, reactions_mode, with_threads)


def _output_message_text(
    msg: Message,
    reactions_mode: str,
    with_threads: bool,
    indent_level: int = 0,
) -> None:
    """Output a single message as formatted text.

    Args:
        msg: The Message to display.
        reactions_mode: How to display reactions.
        with_threads: Whether to display inline thread replies.
        indent_level: Indentation level (0 = top-level, 1 = reply).
    """
    base_indent = "    " * indent_level
    text_indent = base_indent + "  "

    # Build the header line
    user_name = format_user_name(msg.user_name, msg.user_id)
    print(f"{base_indent}{msg.datetime_str}  {user_name}")

    # Print message text (or file attachments if no text)
    if msg.text:
        print(format_message_text(msg.text, indent=text_indent))
    elif msg.files:
        # No text but has files - will be printed below
        pass
    else:
        print(format_message_text("", indent=text_indent))

    # Print file attachments
    files_str = format_files(msg.files, indent=text_indent)
    if files_str:
        print(files_str)

    # Print metadata line (replies, reactions)
    meta_parts = []
    if msg.reply_count > 0:
        if with_threads and msg.replies:
            meta_parts.append(f"[{msg.reply_count} replies]")
        else:
            meta_parts.append(f"[{msg.reply_count} replies, thread_ts={msg.ts}]")

    reactions_str = format_reactions(msg.reactions, reactions_mode)
    if reactions_str:
        meta_parts.append(reactions_str)

    if meta_parts:
        print(f"{text_indent}{' '.join(meta_parts)}")

    # Display inline thread replies if present
    if with_threads and msg.replies:
        print()  # Blank line before replies
        for reply in msg.replies:
            _output_message_text(reply, reactions_mode, with_threads=False, indent_level=indent_level + 1)

    print()  # Blank line between messages


def output_thread_text(
    messages: list[Message],
    reactions_mode: str = "off",
) -> None:
    """Output thread messages as formatted text.

    Args:
        messages: List of messages (parent first, then replies).
        reactions_mode: How to display reactions.
    """
    if not messages:
        return

    # First message is the parent
    parent = messages[0]
    replies = messages[1:]

    # Display parent
    user_name = format_user_name(parent.user_name, parent.user_id)
    print(f"{parent.datetime_str}  {user_name} [parent]")
    print(format_message_text(parent.text))

    files_str = format_files(parent.files, indent="  ")
    if files_str:
        print(files_str)

    reactions_str = format_reactions(parent.reactions, reactions_mode)
    if reactions_str:
        print(f"  {reactions_str}")

    print()  # Blank line after parent

    # Display replies (indented)
    for reply in replies:
        user_name = format_user_name(reply.user_name, reply.user_id)
        print(f"  {reply.datetime_str}  {user_name}")
        print(format_message_text(reply.text, indent="    "))

        files_str = format_files(reply.files, indent="    ")
        if files_str:
            print(files_str)

        reactions_str = format_reactions(reply.reactions, reactions_mode)
        if reactions_str:
            print(f"    {reactions_str}")

        print()  # Blank line between replies


def output_resolved_message_json(resolved: ResolvedMessage) -> None:
    """Output a resolved message as JSON.

    Args:
        resolved: The ResolvedMessage to serialize.
    """
    output_json(resolved.to_dict())


def output_resolved_message_text(resolved: ResolvedMessage) -> None:
    """Output a resolved message as formatted text.

    Args:
        resolved: The ResolvedMessage to display.
    """
    print(f"Channel: #{resolved.channel_name} ({resolved.channel_id})")

    if resolved.is_thread_reply and resolved.thread_ts:
        print(f"Thread: {resolved.thread_ts}")
        print(f"  To view full thread: slack messages '#{resolved.channel_name}' {resolved.thread_ts}")
        print(f"Message: {resolved.message_ts} (reply in thread)")
    else:
        print(f"Message: {resolved.message_ts}")

    print()

    # Display the message
    msg = resolved.message
    user_name = format_user_name(msg.user_name, msg.user_id)
    print(f"{msg.datetime_str}  {user_name}")
    print(format_message_text(msg.text))

    files_str = format_files(msg.files)
    if files_str:
        print(files_str)


def output_conversations_text(
    conversations: list[Conversation],
    users: dict[str, str],
) -> None:
    """Output conversations as formatted text.

    Args:
        conversations: List of conversations to display.
        users: Dictionary mapping user ID to display name.
    """

    def get_display_name(convo: Conversation) -> str:
        """Get the display name for a conversation."""
        if convo.is_im and convo.user_id:
            return users.get(convo.user_id, convo.user_id)
        if convo.is_mpim and convo.member_ids:
            # Sort member names alphabetically
            member_names = sorted(users.get(uid, uid) for uid in convo.member_ids)
            return ", ".join(member_names)
        return convo.name or "(no name)"

    # Sort by type and name
    sorted_convos = sorted(
        conversations,
        key=lambda c: (
            0 if c.is_channel and not c.is_private else 1 if c.is_channel else 2 if c.is_group else 3,
            get_display_name(c).lower(),
        ),
    )

    for convo in sorted_convos:
        display_name = get_display_name(convo)
        convo_type = convo.get_type()
        print(f"{convo.id}: {display_name} ({convo_type})")

    console.print(f"\n[dim]Total: {len(conversations)} conversations[/dim]")
