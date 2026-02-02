"""Slack Block Kit rendering for CLI output.

This module provides simplified text rendering of Slack Block Kit messages,
including blocks, attachments, and rich text elements.
"""

from typing import Any


def render_rich_text_element(element: dict[str, Any], users: dict[str, str], channels: dict[str, str]) -> str:
    """Render a single rich text element to plain text.

    Args:
        element: The rich text element (text, user, channel, link, emoji, broadcast).
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Plain text representation.
    """
    elem_type = element.get("type", "")

    if elem_type == "text":
        return element.get("text", "")

    if elem_type == "user":
        user_id = element.get("user_id", "")
        user_name = users.get(user_id, user_id)
        return f"@{user_name}"

    if elem_type == "channel":
        channel_id = element.get("channel_id", "")
        channel_name = channels.get(channel_id, channel_id)
        return f"#{channel_name}"

    if elem_type == "link":
        url = element.get("url", "")
        text = element.get("text")
        if text:
            return f"{text} ({url})"
        return url

    if elem_type == "emoji":
        name = element.get("name", "")
        return f":{name}:"

    if elem_type == "broadcast":
        # @here, @channel, @everyone
        range_val = element.get("range", "")
        return f"@{range_val}"

    if elem_type == "usergroup":
        usergroup_id = element.get("usergroup_id", "")
        return f"@usergroup-{usergroup_id}"

    # Unknown element type, return empty
    return ""


def render_rich_text_section(
    section: dict[str, Any],
    users: dict[str, str],
    channels: dict[str, str],
    indent: str = "",
) -> str:
    """Render a rich_text_section element.

    Args:
        section: The rich_text_section element with 'elements' array.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.
        indent: Optional indentation prefix.

    Returns:
        Plain text representation.
    """
    parts = []
    for elem in section.get("elements", []):
        parts.append(render_rich_text_element(elem, users, channels))
    text = "".join(parts)
    if indent and text:
        return indent + text
    return text


def render_rich_text_list(
    lst: dict[str, Any],
    users: dict[str, str],
    channels: dict[str, str],
    base_indent: str = "",
) -> str:
    """Render a rich_text_list element.

    Args:
        lst: The rich_text_list element with 'elements' array and 'style'.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.
        base_indent: Base indentation for nested lists.

    Returns:
        Plain text representation with bullet points or numbers.
    """
    style = lst.get("style", "bullet")
    indent_level = lst.get("indent", 0)
    elements = lst.get("elements", [])

    lines = []
    indent = base_indent + "  " * indent_level

    for i, elem in enumerate(elements):
        prefix = f"{i + 1}. " if style == "ordered" else ("- " if indent_level == 0 else "  - ")

        # Each element in a list is typically a rich_text_section
        if elem.get("type") == "rich_text_section":
            text = render_rich_text_section(elem, users, channels)
            lines.append(f"{indent}{prefix}{text}")
        else:
            # Fallback for other element types
            text = render_rich_text_element(elem, users, channels)
            lines.append(f"{indent}{prefix}{text}")

    return "\n".join(lines)


def render_rich_text_quote(
    quote: dict[str, Any],
    users: dict[str, str],
    channels: dict[str, str],
) -> str:
    """Render a rich_text_quote element.

    Args:
        quote: The rich_text_quote element with 'elements' array.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Plain text representation with quote markers.
    """
    parts = []
    for elem in quote.get("elements", []):
        parts.append(render_rich_text_element(elem, users, channels))
    text = "".join(parts)
    # Add quote markers to each line
    lines = text.split("\n")
    return "\n".join(f"> {line}" for line in lines)


def render_rich_text_preformatted(
    pre: dict[str, Any],
    users: dict[str, str],
    channels: dict[str, str],
) -> str:
    """Render a rich_text_preformatted element.

    Args:
        pre: The rich_text_preformatted element with 'elements' array.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Plain text representation with code block markers.
    """
    parts = []
    for elem in pre.get("elements", []):
        parts.append(render_rich_text_element(elem, users, channels))
    text = "".join(parts)
    return f"```\n{text}\n```"


def render_rich_text_block(
    block: dict[str, Any],
    users: dict[str, str],
    channels: dict[str, str],
) -> str:
    """Render a rich_text block to plain text.

    Args:
        block: The rich_text block with 'elements' array.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Plain text representation.
    """
    parts = []
    for elem in block.get("elements", []):
        elem_type = elem.get("type", "")

        if elem_type == "rich_text_section":
            text = render_rich_text_section(elem, users, channels)
            if text:
                parts.append(text)

        elif elem_type == "rich_text_list":
            text = render_rich_text_list(elem, users, channels)
            if text:
                parts.append(text)

        elif elem_type == "rich_text_quote":
            text = render_rich_text_quote(elem, users, channels)
            if text:
                parts.append(text)

        elif elem_type == "rich_text_preformatted":
            text = render_rich_text_preformatted(elem, users, channels)
            if text:
                parts.append(text)

    return "\n".join(parts)


def render_section_block(block: dict[str, Any]) -> str:
    """Render a section block to plain text.

    Args:
        block: The section block with 'text' field.

    Returns:
        Plain text representation.
    """
    text_obj = block.get("text", {})
    if isinstance(text_obj, dict):
        return text_obj.get("text", "")
    return str(text_obj) if text_obj else ""


def render_context_block(block: dict[str, Any]) -> str:
    """Render a context block to plain text.

    Args:
        block: The context block with 'elements' array.

    Returns:
        Plain text representation.
    """
    parts = []
    for elem in block.get("elements", []):
        elem_type = elem.get("type", "")
        if elem_type in ("plain_text", "mrkdwn"):
            parts.append(elem.get("text", ""))
        elif elem_type == "image":
            alt_text = elem.get("alt_text", "image")
            parts.append(f"[{alt_text}]")
    return " | ".join(parts)


def render_header_block(block: dict[str, Any]) -> str:
    """Render a header block to plain text.

    Args:
        block: The header block with 'text' field.

    Returns:
        Plain text representation.
    """
    text_obj = block.get("text", {})
    text = text_obj.get("text", "") if isinstance(text_obj, dict) else (str(text_obj) if text_obj else "")
    return f"## {text}" if text else ""


def render_image_block(block: dict[str, Any]) -> str:
    """Render an image block to plain text.

    Args:
        block: The image block with 'alt_text' and 'image_url'.

    Returns:
        Plain text representation.
    """
    alt_text = block.get("alt_text", "image")
    title = block.get("title", {})
    title_text = title.get("text", "") if isinstance(title, dict) else (str(title) if title else "")

    if title_text:
        return f"[Image: {title_text}]"
    return f"[Image: {alt_text}]"


def render_actions_block(block: dict[str, Any]) -> str:
    """Render an actions block to plain text.

    Args:
        block: The actions block with 'elements' array of buttons/selects.

    Returns:
        Plain text representation.
    """
    parts = []
    for elem in block.get("elements", []):
        elem_type = elem.get("type", "")
        if elem_type == "button":
            text = elem.get("text", {})
            if isinstance(text, dict):
                parts.append(f"[{text.get('text', 'Button')}]")
            else:
                parts.append(f"[{text or 'Button'}]")
        elif elem_type in ("static_select", "external_select", "users_select", "channels_select"):
            placeholder = elem.get("placeholder", {})
            if isinstance(placeholder, dict):
                parts.append(f"[Select: {placeholder.get('text', 'Select')}]")
            else:
                parts.append("[Select]")
    return " ".join(parts)


def render_block(
    block: dict[str, Any],
    users: dict[str, str],
    channels: dict[str, str],
) -> str:
    """Render a single Block Kit block to plain text.

    Args:
        block: The block to render.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Plain text representation.
    """
    block_type = block.get("type", "")

    if block_type == "rich_text":
        return render_rich_text_block(block, users, channels)
    if block_type == "section":
        return render_section_block(block)
    if block_type == "context":
        return render_context_block(block)
    if block_type == "header":
        return render_header_block(block)
    if block_type == "divider":
        return "---"
    if block_type == "image":
        return render_image_block(block)
    if block_type == "actions":
        return render_actions_block(block)

    # Unknown block type
    return ""


def render_blocks(
    blocks: list[dict[str, Any]],
    users: dict[str, str],
    channels: dict[str, str],
) -> str:
    """Render a list of Block Kit blocks to plain text.

    Args:
        blocks: List of blocks to render.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Plain text representation.
    """
    parts = []
    for block in blocks:
        text = render_block(block, users, channels)
        if text:
            parts.append(text)
    return "\n".join(parts)


def render_attachment(
    attachment: dict[str, Any],
    users: dict[str, str],
    channels: dict[str, str],
) -> str:
    """Render a Slack attachment to plain text.

    Args:
        attachment: The attachment to render.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Plain text representation.
    """
    parts = []

    # Title with optional link
    title = attachment.get("title", "")
    title_link = attachment.get("title_link", "")
    if title:
        if title_link:
            parts.append(f"{title} ({title_link})")
        else:
            parts.append(title)

    # Author info
    author_name = attachment.get("author_name", "")
    if author_name:
        parts.append(f"by {author_name}")

    # Pretext
    pretext = attachment.get("pretext", "")
    if pretext:
        parts.append(pretext)

    # Main text or fallback
    text = attachment.get("text", "") or attachment.get("fallback", "")
    if text:
        parts.append(text)

    # Fields
    fields = attachment.get("fields", [])
    for field in fields:
        field_title = field.get("title", "")
        field_value = field.get("value", "")
        if field_title and field_value:
            parts.append(f"{field_title}: {field_value}")
        elif field_value:
            parts.append(field_value)

    # Blocks inside attachment
    blocks = attachment.get("blocks", [])
    if blocks:
        blocks_text = render_blocks(blocks, users, channels)
        if blocks_text:
            parts.append(blocks_text)

    # Message blocks (for message unfurls)
    message_blocks = attachment.get("message_blocks", [])
    for msg_block in message_blocks:
        message = msg_block.get("message", {})
        inner_blocks = message.get("blocks", [])
        if inner_blocks:
            blocks_text = render_blocks(inner_blocks, users, channels)
            if blocks_text:
                parts.append(blocks_text)

    # From URL (source link)
    from_url = attachment.get("from_url", "")
    if from_url and from_url not in parts:
        parts.append(from_url)

    return "\n".join(parts)


def render_attachments(
    attachments: list[dict[str, Any]],
    users: dict[str, str],
    channels: dict[str, str],
) -> str:
    """Render a list of attachments to plain text.

    Args:
        attachments: List of attachments to render.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Plain text representation.
    """
    parts = []
    for attachment in attachments:
        text = render_attachment(attachment, users, channels)
        if text:
            parts.append(text)
    return "\n---\n".join(parts)


def get_message_text(
    message: dict[str, Any],
    users: dict[str, str],
    channels: dict[str, str],
) -> str:
    """Extract text from a message, preferring blocks over plain text.

    Slack Block Kit messages typically have both a 'text' field (fallback/summary)
    and a 'blocks' field (rich content). For proper rendering, we should prefer
    blocks when available, as they contain the actual formatted content.

    Args:
        message: The message object from the Slack API.
        users: Dictionary mapping user ID to display name.
        channels: Dictionary mapping channel ID to channel name.

    Returns:
        Text content of the message.
    """
    # First try to render blocks (preferred source for Block Kit messages)
    blocks = message.get("blocks", [])
    if blocks:
        blocks_text = render_blocks(blocks, users, channels)
        if blocks_text.strip():
            return blocks_text

    # Then try the plain text field
    text = message.get("text", "").strip()
    if text:
        return text

    # Finally, try attachments
    attachments = message.get("attachments", [])
    if attachments:
        attachments_text = render_attachments(attachments, users, channels)
        if attachments_text.strip():
            return attachments_text

    # No content found
    return ""
