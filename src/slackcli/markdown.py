"""Markdown to Slack rich text conversion.

Agents write Markdown, Slack renders Block Kit. This module detects whether a message
body is Markdown at all, and turns one into a single ``rich_text`` block: real bullet
lists, syntax-highlighted code, quotes and inline styles, encoded the way Slack itself
encodes a message composed in its own editor.

Slack's own tokens survive the conversion: ``<@U123>``, ``<#C123|name>``, ``<!here>``
and ``:emoji:`` become the rich text elements Slack renders as mentions and emoji.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token

from .logging import get_logger

logger = get_logger(__name__)

# Signals that a body was written as Markdown rather than as Slack's own mrkdwn.
# Anything both dialects share (*x*, _x_, a bare ``` fence, > quote, <url|label>,
# <@U123>) is deliberately absent: it must not push a plain message into Markdown mode.
_MARKDOWN_SIGNALS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\*\*[^*\s](?:[^*]*[^*\s])?\*\*"),  # **bold**
    re.compile(r"~~[^~\s](?:[^~]*[^~\s])?~~"),  # ~~strikethrough~~
    re.compile(r"\[[^\]]*\]\((?:https?://|mailto:)[^)\s]*\)"),  # [label](url)
    re.compile(r"^[ \t]{0,3}#{1,6} ", re.M),  # # heading
    re.compile(r"^[ \t]*[-*+] ", re.M),  # - bullet item
    re.compile(r"^[ \t]*\d+[.)] ", re.M),  # 1. ordered item
    re.compile(r"^[ \t]*```[a-zA-Z]", re.M),  # ```language fence
    re.compile(r"^\|.+\|[ \t]*$", re.M),  # | table | row |
)

# Slack's own inline tokens, which are kept as rich text elements instead of plain text.
# An emoji shortcode either holds a letter, or is one of Slack's numeric ones (:+1:, :100:);
# the numeric form may not sit between digits, so a time like 10:30:45 is left alone.
_SLACK_TOKENS = re.compile(
    r"<@(?P<user>[UW][A-Z0-9]+)(?:\|[^>]*)?>"
    r"|<#(?P<channel>C[A-Z0-9]+)(?:\|[^>]*)?>"
    r"|<!subteam\^(?P<usergroup>S[A-Z0-9]+)(?:\|[^>]*)?>"
    r"|<!(?P<broadcast>here|channel|everyone)(?:\|[^>]*)?>"
    r"|:(?P<emoji>[a-z0-9_+-]*[a-z][a-z0-9_+-]*):"
    r"|(?<!\d):(?P<emoji_number>[+-]?\d+):(?!\d)"
)

# A run that looks like a URL, inside which colons never start an emoji shortcode.
_URL_RUN = re.compile(r"\S+://\S+")

# A labelled Slack link. Left to markdown-it it becomes an autolink with the pipe
# percent-encoded and the label lost, so an inline rule claims it first.
_SLACK_LINK = re.compile(r"<(?P<url>https?://[^|>\s]+)\|(?P<label>[^>]*)>")

# A labelled broadcast or user group. markdown-it reads the label's @ as an email
# address and turns the whole token into a mailto: link, so the same inline rule
# claims it and drops the label; nothing is lost, since Slack renders the token itself.
_SLACK_LABELLED_TOKEN = re.compile(r"<!(?P<token>subteam\^S[A-Z0-9]+|here|channel|everyone)\|[^>]*>")

# Link targets Slack accepts in a link element. Anything else is shown as text.
_LINKABLE = re.compile(r"^(?:https?://|mailto:)", re.I)

_STYLE_OPEN = {"strong_open": "bold", "em_open": "italic", "s_open": "strike"}
_STYLE_CLOSE = {"strong_close": "bold", "em_close": "italic", "s_close": "strike"}


def looks_like_markdown(text: str) -> bool:
    """Tell whether a message body was written as Markdown.

    Only syntax that Slack's mrkdwn does not share counts as a signal, so a plain Slack
    message with *bold*, _italic_ or a <url|label> link is not mistaken for Markdown.

    Args:
        text: The message body.

    Returns:
        True if the body carries at least one Markdown signal.
    """
    if not text:
        return False

    return any(pattern.search(text) for pattern in _MARKDOWN_SIGNALS)


def markdown_to_blocks(text: str) -> list[dict[str, Any]]:
    """Convert a Markdown body into Slack Block Kit blocks.

    The whole body becomes one rich_text block whose elements are sections, lists,
    preformatted code and quotes, in the order they appear. Anything that cannot be
    represented, such as a table, is rendered as monospace text so that nothing is lost.

    This never raises: a body it cannot parse comes back as an empty list, and the
    caller falls back to sending the text as it stands.

    Args:
        text: The message body, in Markdown.

    Returns:
        A single-element list holding the rich_text block, or an empty list when the
        body carries no renderable content.
    """
    if not text or not text.strip():
        return []

    try:
        elements = _Converter(text).convert()
    except Exception:  # noqa: BLE001 - a converter bug must not stop a message from being sent
        logger.debug("Markdown conversion failed, falling back to plain text", exc_info=True)
        return []

    if not elements:
        return []

    return [{"type": "rich_text", "elements": elements}]


def _text_element(text: str, style: dict[str, bool] | None = None) -> dict[str, Any]:
    """Build a rich text ``text`` element.

    Args:
        text: The literal text.
        style: Active inline styles, if any.

    Returns:
        The element.
    """
    element: dict[str, Any] = {"type": "text", "text": text}
    if style:
        element["style"] = dict(style)
    return element


def _link_element(url: str, label: str, style: dict[str, bool]) -> dict[str, Any]:
    """Build a rich text element for a link.

    Slack only takes a real URL in a link element, so a target it cannot open — a
    heading anchor, a relative path — is shown as text instead of being dropped.

    Args:
        url: The link target.
        label: The text shown for the link.
        style: Active inline styles.

    Returns:
        A link element, or a text element when the target is not a URL.
    """
    if not _LINKABLE.match(url):
        return _text_element(f"{label} ({url})" if label else url, style)

    element: dict[str, Any] = {"type": "link", "url": url}
    if label and label != url:
        element["text"] = label
    if style:
        element["style"] = dict(style)
    return element


def _slack_syntax_rule(state: StateInline, silent: bool) -> bool:
    """Claim the Slack syntax that the Markdown parser would otherwise mangle.

    This runs as a markdown-it inline rule, ahead of the autolink rule, so markdown-it
    itself decides where it applies: a fenced block never reaches inline parsing at
    all, and a code span is claimed by the backticks rule before this one is tried.
    Slack syntax inside code therefore stays exactly as it was written, without this
    module having to work out where code begins and ends.

    Args:
        state: The inline parser state, positioned on a "<".
        silent: True while the parser is only probing, when no token may be pushed.

    Returns:
        True if the rule consumed a Slack token at the current position.
    """
    if state.src[state.pos] != "<":
        return False

    link = _SLACK_LINK.match(state.src, state.pos)
    if link:
        if not silent:
            opening = state.push("link_open", "a", 1)
            opening.attrSet("href", link.group("url"))
            label = state.push("text", "", 0)
            label.content = link.group("label")
            state.push("link_close", "a", -1)
        state.pos = link.end()
        return True

    labelled = _SLACK_LABELLED_TOKEN.match(state.src, state.pos)
    if labelled:
        if not silent:
            # Re-emitted without the label, for _split_slack_tokens to turn into an element
            token = state.push("text", "", 0)
            token.content = f"<!{labelled.group('token')}>"
        state.pos = labelled.end()
        return True

    return False


def _parser() -> MarkdownIt:
    """Build the Markdown parser, with Slack's own syntax taught to it.

    Returns:
        A parser for CommonMark plus strikethrough, tables and Slack syntax.
    """
    parser = MarkdownIt("commonmark", {"html": False}).enable(["strikethrough", "table"])
    parser.inline.ruler.before("autolink", "slack_syntax", _slack_syntax_rule)
    return parser


def _split_slack_tokens(text: str, style: dict[str, bool]) -> list[dict[str, Any]]:
    """Split literal text into text elements and the Slack tokens embedded in it.

    Args:
        text: The literal text of one inline run.
        style: Active inline styles.

    Returns:
        The elements the run turns into.
    """
    elements: list[dict[str, Any]] = []
    url_runs = [match.span() for match in _URL_RUN.finditer(text)]
    position = 0

    for match in _SLACK_TOKENS.finditer(text):
        emoji = match.group("emoji") or match.group("emoji_number")
        if emoji and any(start <= match.start() < end for start, end in url_runs):
            # A colon inside a URL is part of the address, not an emoji shortcode
            continue

        if match.start() > position:
            elements.append(_text_element(text[position : match.start()], style))

        if match.group("user"):
            elements.append({"type": "user", "user_id": match.group("user")})
        elif match.group("channel"):
            elements.append({"type": "channel", "channel_id": match.group("channel")})
        elif match.group("usergroup"):
            elements.append({"type": "usergroup", "usergroup_id": match.group("usergroup")})
        elif match.group("broadcast"):
            elements.append({"type": "broadcast", "range": match.group("broadcast")})
        else:
            elements.append({"type": "emoji", "name": emoji})

        position = match.end()

    if position < len(text):
        elements.append(_text_element(text[position:], style))

    return elements


def _plain_text(tokens: list[Token]) -> str:
    """Flatten inline tokens to their literal text, for a link label.

    Args:
        tokens: The inline tokens making up the label.

    Returns:
        The text they carry.
    """
    parts = []
    for token in tokens:
        if token.type in ("text", "code_inline", "html_inline"):
            parts.append(token.content)
        elif token.type in ("softbreak", "hardbreak"):
            parts.append(" ")
        elif token.type == "image":
            parts.append(token.content)
    return "".join(parts)


def _close_index(tokens: list[Token], index: int) -> int:
    """Find the token that closes the opening token at the given index.

    Args:
        tokens: The token list.
        index: Index of an opening token.

    Returns:
        Index of the matching closing token, or the last index when it is missing.
    """
    level = tokens[index].level
    for candidate in range(index + 1, len(tokens)):
        if tokens[candidate].nesting == -1 and tokens[candidate].level == level:
            return candidate
    return len(tokens) - 1


def _render_inline(token: Token | None, base_style: dict[str, bool] | None = None) -> list[dict[str, Any]]:
    """Render one inline token into rich text elements.

    Args:
        token: The inline token, or None when the block carries no inline content.
        base_style: Styles that apply to the whole run, such as bold for a heading.

    Returns:
        The elements the inline content turns into.
    """
    if token is None or not token.children:
        return []

    elements: list[dict[str, Any]] = []
    depth: dict[str, int] = dict.fromkeys(base_style or {}, 1)
    children = token.children

    def active_style() -> dict[str, bool]:
        return {name: True for name, count in depth.items() if count > 0}

    index = 0
    while index < len(children):
        child = children[index]
        kind = child.type

        if kind in ("text", "html_inline"):
            elements.extend(_split_slack_tokens(child.content, active_style()))
        elif kind == "code_inline":
            elements.append(_text_element(child.content, {**active_style(), "code": True}))
        elif kind in ("softbreak", "hardbreak"):
            elements.append(_text_element("\n"))
        elif kind in _STYLE_OPEN:
            name = _STYLE_OPEN[kind]
            depth[name] = depth.get(name, 0) + 1
        elif kind in _STYLE_CLOSE:
            name = _STYLE_CLOSE[kind]
            depth[name] = max(depth.get(name, 0) - 1, 0)
        elif kind == "link_open":
            close = _close_index(children, index)
            label = _plain_text(children[index + 1 : close])
            elements.append(_link_element(child.attrGet("href") or "", label, active_style()))
            index = close
        elif kind == "image":
            elements.append(_link_element(child.attrGet("src") or "", child.content, active_style()))

        index += 1

    return [element for element in elements if element.get("type") != "text" or element["text"]]


class _Converter:
    """Walks a Markdown token stream and builds the elements of one rich_text block."""

    def __init__(self, text: str) -> None:
        """Parse the body.

        Args:
            text: The message body, in Markdown.
        """
        # Normalise line endings the way markdown-it does, and parse that same string,
        # so the line numbers in a token's map index straight into source_lines
        source = text.replace("\r\n", "\n").replace("\r", "\n")
        self.source_lines = source.split("\n")
        self.tokens = _parser().parse(source)
        self.elements: list[dict[str, Any]] = []
        self._section: list[dict[str, Any]] = []
        self._needs_leading_newline = False

    def convert(self) -> list[dict[str, Any]]:
        """Render the whole document.

        Returns:
            The elements of the rich_text block.
        """
        self._walk(0, len(self.tokens))
        self._flush_section()
        return self.elements

    # -- section handling ---------------------------------------------------

    def _add_paragraph(self, elements: list[dict[str, Any]]) -> None:
        """Append a paragraph to the section being built.

        Consecutive paragraphs share one section, separated by a blank line, which is
        how Slack encodes a message written in its own editor.

        Args:
            elements: The inline elements of the paragraph.
        """
        if not elements:
            return

        if self._section:
            self._section.append(_text_element("\n\n"))
        elif self._needs_leading_newline:
            self._section.append(_text_element("\n"))
            self._needs_leading_newline = False

        self._section.extend(elements)

    def _flush_section(self) -> None:
        """Close the section being built, if it has any content."""
        if self._section:
            self.elements.append({"type": "rich_text_section", "elements": self._section})
            self._section = []

    def _add_element(self, element: dict[str, Any]) -> None:
        """Append a list, code block or quote, ending the section before it.

        Args:
            element: The element to append.
        """
        self._flush_section()
        self.elements.append(element)
        self._needs_leading_newline = True

    # -- block walking ------------------------------------------------------

    def _walk(self, start: int, end: int) -> None:
        """Render the block-level tokens in a range.

        Args:
            start: First token index.
            end: Index past the last token.
        """
        index = start
        while index < end:
            token = self.tokens[index]
            kind = token.type

            if kind == "paragraph_open":
                close = _close_index(self.tokens, index)
                self._add_paragraph(_render_inline(self._inline_of(index, close)))
                index = close + 1
            elif kind == "heading_open":
                close = _close_index(self.tokens, index)
                self._add_paragraph(_render_inline(self._inline_of(index, close), {"bold": True}))
                index = close + 1
            elif kind in ("bullet_list_open", "ordered_list_open"):
                close = _close_index(self.tokens, index)
                self._render_list(index, close, indent=0)
                index = close + 1
            elif kind in ("fence", "code_block"):
                self._add_preformatted(token)
                index += 1
            elif kind == "blockquote_open":
                close = _close_index(self.tokens, index)
                self._add_quote(index + 1, close)
                index = close + 1
            elif kind == "hr":
                self._add_paragraph([_text_element("───")])
                index += 1
            elif kind == "table_open":
                close = _close_index(self.tokens, index)
                self._add_table(token)
                index = close + 1
            else:
                index += 1

    def _inline_of(self, open_index: int, close_index: int) -> Token | None:
        """Find the inline token between an opening and closing token.

        Args:
            open_index: Index of the opening token.
            close_index: Index of the closing token.

        Returns:
            The inline token, or None when there is none.
        """
        for index in range(open_index + 1, close_index):
            if self.tokens[index].type == "inline":
                return self.tokens[index]
        return None

    def _add_preformatted(self, token: Token) -> None:
        """Append a code block.

        Args:
            token: A fence or indented code_block token.
        """
        content = token.content
        if content.endswith("\n"):
            content = content[:-1]
        if not content:
            return

        element: dict[str, Any] = {
            "type": "rich_text_preformatted",
            "elements": [_text_element(content)],
        }

        info = (token.info or "").strip()
        if info:
            element["language"] = info.split()[0]

        self._add_element(element)

    def _source_of(self, token: Token) -> str:
        """Read back the source lines a token was built from.

        Args:
            token: A block token carrying a source map.

        Returns:
            The lines as they were written, or an empty string when the token has no map.
        """
        if not token.map:
            return ""

        start, end = token.map
        return "\n".join(self.source_lines[start:end]).rstrip()

    def _add_table(self, token: Token) -> None:
        """Append a table as monospace text, so its columns stay aligned.

        Args:
            token: The table_open token, whose map gives the source lines.
        """
        content = self._source_of(token)
        if not content:
            return

        self._add_element({"type": "rich_text_preformatted", "elements": [_text_element(content)]})

    def _add_quote(self, start: int, end: int) -> None:
        """Append a block quote.

        Slack's quotes hold inline content only, so the blocks inside a quote are
        flattened: paragraphs are separated by a blank line, list items become bulleted
        lines, and code keeps its own lines.

        Args:
            start: First token index inside the quote.
            end: Index past the last token inside the quote.
        """
        parts: list[list[dict[str, Any]]] = []
        index = start

        while index < end:
            token = self.tokens[index]
            kind = token.type

            if kind in ("paragraph_open", "heading_open"):
                close = _close_index(self.tokens, index)
                base_style = {"bold": True} if kind == "heading_open" else None
                rendered = _render_inline(self._inline_of(index, close), base_style)
                if rendered:
                    parts.append(rendered)
                index = close + 1
            elif kind in ("bullet_list_open", "ordered_list_open"):
                close = _close_index(self.tokens, index)
                lines = self._quoted_list_lines(index + 1, close)
                if lines:
                    parts.append(lines)
                index = close + 1
            elif kind in ("fence", "code_block"):
                content = token.content.rstrip("\n")
                if content:
                    parts.append([_text_element(content)])
                index += 1
            elif kind == "table_open":
                close = _close_index(self.tokens, index)
                content = self._source_of(token)
                if content:
                    parts.append([_text_element(content)])
                index = close + 1
            else:
                index += 1

        if not parts:
            return

        elements: list[dict[str, Any]] = []
        for position, part in enumerate(parts):
            if position:
                elements.append(_text_element("\n\n"))
            elements.extend(part)

        self._add_element({"type": "rich_text_quote", "elements": elements})

    def _quoted_list_lines(self, start: int, end: int) -> list[dict[str, Any]]:
        """Render the items of a list inside a quote as bulleted lines.

        Args:
            start: First token index inside the list.
            end: Index past the last token inside the list.

        Returns:
            The inline elements of the lines, separated by newlines.
        """
        lines: list[list[dict[str, Any]]] = []
        index = start

        # Every item counts, however deeply nested: a Slack quote holds no list structure,
        # so stepping over a nested list would drop its items entirely
        while index < end:
            if self.tokens[index].type == "list_item_open":
                close = _close_index(self.tokens, index)
                rendered = _render_inline(self._inline_of(index, close))
                if rendered:
                    lines.append([_text_element("• "), *rendered])
            index += 1

        elements: list[dict[str, Any]] = []
        for position, line in enumerate(lines):
            if position:
                elements.append(_text_element("\n"))
            elements.extend(line)
        return elements

    # -- lists --------------------------------------------------------------

    def _render_list(self, open_index: int, close_index: int, indent: int) -> None:
        """Render a list, and everything nested inside its items.

        Slack has no nested lists: a nested level is a sibling list with a higher
        indent, so the parent list is closed, the nested one is emitted, and the parent
        resumes as a new list carrying an offset that keeps its numbering going.

        Args:
            open_index: Index of the list opening token.
            close_index: Index of the list closing token.
            indent: Nesting depth, 0 for a top-level list.
        """
        token = self.tokens[open_index]
        ordered = token.type == "ordered_list_open"
        style = "ordered" if ordered else "bullet"

        try:
            offset = max(int(token.attrGet("start") or 1) - 1, 0)
        except (TypeError, ValueError):
            offset = 0

        items: list[dict[str, Any]] = []
        emitted = 0
        index = open_index + 1

        while index < close_index:
            if self.tokens[index].type != "list_item_open":
                index += 1
                continue

            item_close = _close_index(self.tokens, index)
            section, deferred = self._render_item(index + 1, item_close, indent)
            if section is not None:
                items.append(section)

            if deferred:
                # Close this run of items, render what the item nested, then resume the list
                emitted += self._emit_list(items, style, indent, offset + emitted if ordered else 0)
                items = []
                for render in deferred:
                    render()

            index = item_close + 1

        self._emit_list(items, style, indent, offset + emitted if ordered else 0)

    def _emit_list(self, items: list[dict[str, Any]], style: str, indent: int, offset: int) -> int:
        """Append one rich_text_list element.

        Args:
            items: The item sections.
            style: "bullet" or "ordered".
            indent: Nesting depth.
            offset: Number of items that came before, for ordered numbering.

        Returns:
            How many items were emitted.
        """
        if not items:
            return 0

        element: dict[str, Any] = {
            "type": "rich_text_list",
            "style": style,
            "indent": indent,
            "elements": items,
        }
        if offset:
            element["offset"] = offset

        self._add_element(element)
        return len(items)

    def _render_item(self, start: int, end: int, indent: int) -> tuple[dict[str, Any] | None, list[Callable[[], None]]]:
        """Render one list item.

        Args:
            start: First token index inside the item.
            end: Index past the last token inside the item.
            indent: Nesting depth of the list this item belongs to.

        Returns:
            The item's section (or None when the item is empty), and callables that
            render what the item nested, to be run once the list is closed.
        """
        lines: list[list[dict[str, Any]]] = []
        deferred: list[Callable[[], None]] = []
        index = start

        while index < end:
            token = self.tokens[index]
            kind = token.type

            if kind in ("paragraph_open", "heading_open"):
                close = _close_index(self.tokens, index)
                base_style = {"bold": True} if kind == "heading_open" else None
                rendered = _render_inline(self._inline_of(index, close), base_style)
                if rendered:
                    lines.append(rendered)
                index = close + 1
            elif kind in ("bullet_list_open", "ordered_list_open"):
                close = _close_index(self.tokens, index)
                deferred.append(
                    lambda open_index=index, close_index=close: self._render_list(open_index, close_index, indent + 1)
                )
                index = close + 1
            elif kind in ("fence", "code_block"):
                deferred.append(lambda code=token: self._add_preformatted(code))
                index += 1
            elif kind == "table_open":
                close = _close_index(self.tokens, index)
                deferred.append(lambda table=token: self._add_table(table))
                index = close + 1
            elif kind == "blockquote_open":
                close = _close_index(self.tokens, index)
                deferred.append(lambda quote_start=index + 1, quote_end=close: self._add_quote(quote_start, quote_end))
                index = close + 1
            else:
                index += 1

        if not lines:
            return None, deferred

        elements: list[dict[str, Any]] = []
        for position, line in enumerate(lines):
            if position:
                elements.append(_text_element("\n"))
            elements.extend(line)

        return {"type": "rich_text_section", "elements": elements}, deferred
