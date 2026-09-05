"""Tests for Markdown to Slack rich text conversion."""

from __future__ import annotations

from typing import Any

from slackcli.markdown import looks_like_markdown, markdown_to_blocks


def convert(text: str) -> list[dict[str, Any]]:
    """Convert Markdown and return the elements of the single rich_text block."""
    blocks = markdown_to_blocks(text)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "rich_text"
    return blocks[0]["elements"]


def texts(elements: list[dict[str, Any]]) -> str:
    """Join the literal text of a run of inline elements."""
    return "".join(element.get("text", "") for element in elements)


class TestLooksLikeMarkdown:
    """Tests for looks_like_markdown()."""

    def test_bold(self) -> None:
        assert looks_like_markdown("this is **bold** here")

    def test_strikethrough(self) -> None:
        assert looks_like_markdown("this is ~~gone~~ now")

    def test_link(self) -> None:
        assert looks_like_markdown("see [the docs](https://example.com)")

    def test_heading(self) -> None:
        assert looks_like_markdown("# Title\n\nbody")

    def test_heading_must_have_a_space(self) -> None:
        assert not looks_like_markdown("#channel-name is not a heading")

    def test_bullet_list(self) -> None:
        assert looks_like_markdown("intro\n\n- one\n- two")

    def test_indented_bullet_list(self) -> None:
        assert looks_like_markdown("intro\n\n  - one")

    def test_ordered_list(self) -> None:
        assert looks_like_markdown("1. one\n2. two")

    def test_ordered_list_with_parenthesis(self) -> None:
        assert looks_like_markdown("1) one\n2) two")

    def test_fence_with_a_language(self) -> None:
        assert looks_like_markdown("```sql\nselect 1\n```")

    def test_table(self) -> None:
        assert looks_like_markdown("| a | b |\n|---|---|\n| 1 | 2 |")

    def test_empty_text(self) -> None:
        assert not looks_like_markdown("")

    def test_pure_mrkdwn_message(self) -> None:
        """The mrkdwn dialect must not be mistaken for Markdown."""
        assert not looks_like_markdown("*bold* and _italic_ and <https://example.com|a link>")

    def test_slack_mentions_are_not_a_signal(self) -> None:
        assert not looks_like_markdown("hey <@U0123456789>, see <#C0123456789|general> :wave:")

    def test_bare_fence_is_not_a_signal(self) -> None:
        """A bare fence means the same in both dialects."""
        assert not looks_like_markdown("look:\n```\nselect 1\n```")

    def test_quote_is_not_a_signal(self) -> None:
        assert not looks_like_markdown("> quoted line")

    def test_single_asterisks_are_not_a_signal(self) -> None:
        assert not looks_like_markdown("*not bold in markdown terms*")

    def test_multiplication_is_not_a_signal(self) -> None:
        assert not looks_like_markdown("2 * 3 * 4 = 24")


class TestParagraphs:
    """Tests for paragraphs, headings and inline styles."""

    def test_single_paragraph(self) -> None:
        elements = convert("Just one line.")

        assert len(elements) == 1
        assert elements[0]["type"] == "rich_text_section"
        assert elements[0]["elements"] == [{"type": "text", "text": "Just one line."}]

    def test_paragraphs_coalesce_into_one_section(self) -> None:
        elements = convert("First.\n\nSecond.")

        assert len(elements) == 1
        assert texts(elements[0]["elements"]) == "First.\n\nSecond."

    def test_heading_is_bold(self) -> None:
        elements = convert("# Title\n\nbody")

        first = elements[0]["elements"][0]
        assert first == {"type": "text", "text": "Title", "style": {"bold": True}}

    def test_inline_styles(self) -> None:
        elements = convert("a **b** c *d* e ~~f~~ g `h`")

        styled = {
            element["text"]: element.get("style") for element in elements[0]["elements"] if element["text"].strip()
        }
        assert styled["b"] == {"bold": True}
        assert styled["d"] == {"italic": True}
        assert styled["f"] == {"strike": True}
        assert styled["h"] == {"code": True}

    def test_nested_styles_combine(self) -> None:
        elements = convert("**bold and *italic* inside**")

        combined = [e for e in elements[0]["elements"] if e["text"] == "italic"]
        assert combined[0]["style"] == {"bold": True, "italic": True}

    def test_soft_break_becomes_a_newline(self) -> None:
        elements = convert("**line one**\nline two")

        assert texts(elements[0]["elements"]) == "line one\nline two"

    def test_horizontal_rule(self) -> None:
        elements = convert("above\n\n---\n\nbelow")

        assert "───" in texts(elements[0]["elements"])


class TestLinks:
    """Tests for links, images and Slack's own link syntax."""

    def test_labelled_link(self) -> None:
        elements = convert("see [the docs](https://example.com/a)")

        link = elements[0]["elements"][-1]
        assert link == {"type": "link", "url": "https://example.com/a", "text": "the docs"}

    def test_autolink_omits_the_text(self) -> None:
        """A link whose label is the URL needs no text field."""
        elements = convert("**b** <https://example.com/a>")

        link = elements[0]["elements"][-1]
        assert link == {"type": "link", "url": "https://example.com/a"}

    def test_slack_labelled_link_is_not_double_encoded(self) -> None:
        """Slack's own <url|label> form has to survive the Markdown parser intact."""
        elements = convert("- see <https://example.com/a?x=1|the docs>")

        link = elements[0]["elements"][0]["elements"][-1]
        assert link == {"type": "link", "url": "https://example.com/a?x=1", "text": "the docs"}

    def test_link_keeps_the_surrounding_style(self) -> None:
        elements = convert("**bold [label](https://example.com)**")

        link = [e for e in elements[0]["elements"] if e["type"] == "link"][0]
        assert link["style"] == {"bold": True}

    def test_link_without_a_scheme_becomes_text(self) -> None:
        """Slack rejects a link element that is not a URL, so it is shown as text."""
        elements = convert("**x** [c](#anchor) and [d](/rel/path)")

        assert texts(elements[0]["elements"]) == "x c (#anchor) and d (/rel/path)"
        assert all(element["type"] == "text" for element in elements[0]["elements"])

    def test_mailto_link_is_kept(self) -> None:
        elements = convert("**x** [mail me](mailto:someone@example.com)")

        link = elements[0]["elements"][-1]
        assert link == {"type": "link", "url": "mailto:someone@example.com", "text": "mail me"}

    def test_indexing_syntax_is_not_a_markdown_link(self) -> None:
        """arr[0](1) must not read as a link, and must not trigger Markdown mode."""
        assert not looks_like_markdown("arr[0](1) is an index")

    def test_anchor_link_is_not_a_signal(self) -> None:
        assert not looks_like_markdown("see [c](#anchor)")

    def test_image_becomes_a_link(self) -> None:
        elements = convert("![a chart](https://example.com/chart.png) **x**")

        link = elements[0]["elements"][0]
        assert link == {"type": "link", "url": "https://example.com/chart.png", "text": "a chart"}


class TestSlackTokens:
    """Tests for Slack's mentions and emoji inside Markdown."""

    def test_user_mention(self) -> None:
        elements = convert("**hi** <@U08GTCPJW95>")

        assert elements[0]["elements"][-1] == {"type": "user", "user_id": "U08GTCPJW95"}

    def test_user_mention_with_a_label(self) -> None:
        elements = convert("**hi** <@U08GTCPJW95|filip>")

        assert elements[0]["elements"][-1] == {"type": "user", "user_id": "U08GTCPJW95"}

    def test_channel_mention(self) -> None:
        elements = convert("**see** <#C0142QHKYAV|general>")

        assert elements[0]["elements"][-1] == {"type": "channel", "channel_id": "C0142QHKYAV"}

    def test_broadcast(self) -> None:
        elements = convert("**heads up** <!here>")

        assert elements[0]["elements"][-1] == {"type": "broadcast", "range": "here"}

    def test_labelled_broadcast(self) -> None:
        """The label makes markdown-it read the token as an email address."""
        elements = convert("**heads up** <!here|@here>")

        assert elements[0]["elements"][-1] == {"type": "broadcast", "range": "here"}

    def test_labelled_channel_broadcast(self) -> None:
        elements = convert("**heads up** <!channel|@channel>")

        assert elements[0]["elements"][-1] == {"type": "broadcast", "range": "channel"}

    def test_labelled_everyone_broadcast(self) -> None:
        elements = convert("**heads up** <!everyone|@everyone>")

        assert elements[0]["elements"][-1] == {"type": "broadcast", "range": "everyone"}

    def test_user_group(self) -> None:
        elements = convert("**heads up** <!subteam^S0123ABCD|@backend>")

        assert elements[0]["elements"][-1] == {"type": "usergroup", "usergroup_id": "S0123ABCD"}

    def test_user_group_without_a_label(self) -> None:
        elements = convert("**heads up** <!subteam^S0123ABCD>")

        assert elements[0]["elements"][-1] == {"type": "usergroup", "usergroup_id": "S0123ABCD"}

    def test_numeric_emoji_shortcodes(self) -> None:
        """Slack has emoji whose names hold no letter at all."""
        elements = convert("**x** :+1: :-1: :100: :1234:")

        names = [element["name"] for element in elements[0]["elements"] if element["type"] == "emoji"]
        assert names == ["+1", "-1", "100", "1234"]

    def test_colons_inside_a_url_are_not_emoji(self) -> None:
        elements = convert("**x** https://host/a:b:c")

        assert texts(elements[0]["elements"]) == "x https://host/a:b:c"

    def test_emoji(self) -> None:
        elements = convert("**done** :white_check_mark:")

        assert elements[0]["elements"][-1] == {"type": "emoji", "name": "white_check_mark"}

    def test_a_time_is_not_an_emoji(self) -> None:
        """A shortcode needs a letter, so clock times are left alone."""
        elements = convert("**at** 10:30:45 today")

        assert texts(elements[0]["elements"]) == "at 10:30:45 today"

    def test_tokens_keep_the_surrounding_style(self) -> None:
        elements = convert("**hi <@U08GTCPJW95> there**")

        assert elements[0]["elements"][0]["style"] == {"bold": True}
        assert elements[0]["elements"][1] == {"type": "user", "user_id": "U08GTCPJW95"}


class TestLists:
    """Tests for bullet and ordered lists, including nesting."""

    def test_bullet_list(self) -> None:
        elements = convert("- one\n- two")

        assert len(elements) == 1
        assert elements[0]["type"] == "rich_text_list"
        assert elements[0]["style"] == "bullet"
        assert elements[0]["indent"] == 0
        assert [texts(item["elements"]) for item in elements[0]["elements"]] == ["one", "two"]

    def test_ordered_list(self) -> None:
        elements = convert("1. one\n2. two")

        assert elements[0]["style"] == "ordered"

    def test_ordered_list_start_becomes_an_offset(self) -> None:
        elements = convert("3. three\n4. four")

        assert elements[0]["offset"] == 2

    def test_nested_list_becomes_a_sibling_with_a_higher_indent(self) -> None:
        """Slack has no nested lists, so a nested level is a sibling list."""
        elements = convert("- one\n  - nested\n- two")

        kinds = [(element["type"], element.get("indent")) for element in elements]
        assert kinds == [
            ("rich_text_list", 0),
            ("rich_text_list", 1),
            ("rich_text_list", 0),
        ]
        assert texts(elements[1]["elements"][0]["elements"]) == "nested"

    def test_ordered_list_resumes_with_an_offset_after_a_nested_list(self) -> None:
        elements = convert("1. one\n2. two\n   1. sub\n3. three")

        assert elements[0]["style"] == "ordered"
        assert [texts(item["elements"]) for item in elements[0]["elements"]] == ["one", "two"]
        assert elements[1]["indent"] == 1
        assert elements[2]["offset"] == 2
        assert texts(elements[2]["elements"][0]["elements"]) == "three"

    def test_deeply_nested_lists(self) -> None:
        elements = convert("- a\n  - b\n    - c\n      - d")

        assert [element["indent"] for element in elements] == [0, 1, 2, 3]

    def test_loose_list_item_paragraphs_join_with_a_newline(self) -> None:
        elements = convert("- first line\n\n  second line\n\n- other")

        assert texts(elements[0]["elements"][0]["elements"]) == "first line\nsecond line"

    def test_code_inside_an_item_is_emitted_after_the_list(self) -> None:
        elements = convert("- one\n\n  ```sql\n  select 1\n  ```\n\n- two")

        kinds = [element["type"] for element in elements]
        assert kinds == ["rich_text_list", "rich_text_preformatted", "rich_text_list"]
        assert elements[1]["language"] == "sql"

    def test_heading_inside_an_item_survives(self) -> None:
        elements = convert("- # heading item\n- plain item")

        items = [texts(item["elements"]) for item in elements[0]["elements"]]
        assert items == ["heading item", "plain item"]
        assert elements[0]["elements"][0]["elements"][0]["style"] == {"bold": True}

    def test_table_inside_an_item_is_emitted_after_the_list(self) -> None:
        elements = convert("- item\n\n  | a | b |\n  |---|---|\n  | 1 | 2 |\n\n- other")

        kinds = [element["type"] for element in elements]
        assert kinds == ["rich_text_list", "rich_text_preformatted", "rich_text_list"]
        assert "| 1 | 2 |" in texts(elements[1]["elements"])

    def test_section_after_a_list_starts_with_a_newline(self) -> None:
        """Slack's own encoding separates a list from the text below it this way."""
        elements = convert("- one\n\nafter the list")

        assert elements[1]["type"] == "rich_text_section"
        assert elements[1]["elements"][0] == {"type": "text", "text": "\n"}


class TestCodeBlocks:
    """Tests for fences, indented code and tables."""

    def test_fence_with_a_language(self) -> None:
        elements = convert("```sql\nselect 1\nfrom dual;\n```")

        assert elements[0] == {
            "type": "rich_text_preformatted",
            "elements": [{"type": "text", "text": "select 1\nfrom dual;"}],
            "language": "sql",
        }

    def test_fence_without_a_language(self) -> None:
        elements = convert("**x**\n\n```\nplain\n```")

        assert "language" not in elements[1]

    def test_fence_info_keeps_only_the_first_word(self) -> None:
        elements = convert("```python title=foo.py\nx = 1\n```")

        assert elements[0]["language"] == "python"

    def test_indented_code_block(self) -> None:
        elements = convert("**x**\n\n    indented code\n")

        assert elements[1]["type"] == "rich_text_preformatted"
        assert texts(elements[1]["elements"]) == "indented code"

    def test_table_becomes_monospace(self) -> None:
        elements = convert("| a | b |\n|---|---|\n| 1 | 2 |")

        assert elements[0]["type"] == "rich_text_preformatted"
        assert texts(elements[0]["elements"]) == "| a | b |\n|---|---|\n| 1 | 2 |"

    def test_table_keeps_the_source_alignment(self) -> None:
        source = "| name  | count |\n|-------|-------|\n| alpha |     1 |"
        elements = convert(source)

        assert texts(elements[0]["elements"]) == source


class TestQuotes:
    """Tests for block quotes."""

    def test_quote_paragraph(self) -> None:
        elements = convert("**x**\n\n> quoted line\n> continues")

        assert elements[1]["type"] == "rich_text_quote"
        assert texts(elements[1]["elements"]) == "quoted line\ncontinues"

    def test_quote_paragraphs_join_with_a_blank_line(self) -> None:
        elements = convert("**x**\n\n> one\n>\n> two")

        assert texts(elements[1]["elements"]) == "one\n\ntwo"

    def test_list_inside_a_quote_becomes_bulleted_lines(self) -> None:
        elements = convert("**x**\n\n> intro\n>\n> - one\n> - two")

        assert texts(elements[1]["elements"]) == "intro\n\n• one\n• two"

    def test_heading_inside_a_quote_survives(self) -> None:
        elements = convert("> # Quoted title\n>\n> body")

        assert elements[0]["type"] == "rich_text_quote"
        assert texts(elements[0]["elements"]) == "Quoted title\n\nbody"
        assert elements[0]["elements"][0]["style"] == {"bold": True}

    def test_nested_list_inside_a_quote_survives(self) -> None:
        elements = convert("> - one\n>   - nested\n> - two")

        assert texts(elements[0]["elements"]) == "• one\n• nested\n• two"

    def test_table_inside_a_quote_survives(self) -> None:
        elements = convert("> intro\n>\n> | a | b |\n> |---|---|\n> | 1 | 2 |")

        assert "| 1 | 2 |" in texts(elements[0]["elements"])

    def test_quote_keeps_inline_styles(self) -> None:
        elements = convert("**x**\n\n> quoted **bold**")

        styled = [e for e in elements[1]["elements"] if e.get("style")]
        assert styled[0]["style"] == {"bold": True}


class TestCodeIsLeftAlone:
    """Slack syntax inside code must reach Slack exactly as it was written."""

    def test_fence_keeps_a_slack_link_verbatim(self) -> None:
        elements = convert("**x**\n\n```\ncurl <https://example.com|label>\n```")

        assert texts(elements[1]["elements"]) == "curl <https://example.com|label>"

    def test_inline_code_keeps_a_slack_link_verbatim(self) -> None:
        elements = convert("**x** run `<https://example.com|label>` now")

        code = [element for element in elements[0]["elements"] if element.get("style", {}).get("code")]
        assert code[0]["text"] == "<https://example.com|label>"

    def test_fence_keeps_a_labelled_broadcast_verbatim(self) -> None:
        elements = convert("**x**\n\n```\nping <!here|@here>\n```")

        assert texts(elements[1]["elements"]) == "ping <!here|@here>"

    def test_slack_syntax_after_a_fence_in_a_crlf_document(self) -> None:
        """Where code ends is markdown-it's decision, so CRLF cannot confuse it."""
        elements = convert("**x**\r\n\r\n```\r\ncode\r\n```\r\n\r\nping <!here|@here> and <https://x.com|lab>\r\n")

        assert elements[1]["type"] == "rich_text_preformatted"
        last = elements[2]["elements"]
        assert {"type": "broadcast", "range": "here"} in last
        assert {"type": "link", "url": "https://x.com", "text": "lab"} in last

    def test_slack_syntax_after_a_fence_closed_with_more_backticks(self) -> None:
        """CommonMark lets the closing fence be longer than the opening one."""
        elements = convert("**x**\n\n```\ncode\n````\n\nsee <https://x.com|lab>")

        assert elements[1]["type"] == "rich_text_preformatted"
        assert {"type": "link", "url": "https://x.com", "text": "lab"} in elements[2]["elements"]

    def test_stray_backtick_does_not_swallow_the_rest(self) -> None:
        elements = convert("**x** a ` b <https://x.com|lab>\n\n```\ncode\n```")

        assert {"type": "link", "url": "https://x.com", "text": "lab"} in elements[0]["elements"]
        assert elements[1]["type"] == "rich_text_preformatted"


class TestOddInput:
    """The converter must never raise, whatever it is handed."""

    def test_empty_text(self) -> None:
        assert markdown_to_blocks("") == []

    def test_whitespace_only(self) -> None:
        assert markdown_to_blocks("   \n\n  ") == []

    def test_only_an_empty_fence(self) -> None:
        assert markdown_to_blocks("```\n```") == []

    def test_unclosed_fence(self) -> None:
        elements = convert("```sql\nselect 1")

        assert elements[0]["type"] == "rich_text_preformatted"
        assert elements[0]["language"] == "sql"

    def test_unbalanced_emphasis(self) -> None:
        elements = convert("**never closed and *this one either\n\n- item")

        assert elements[-1]["type"] == "rich_text_list"

    def test_angle_brackets_that_are_not_tokens(self) -> None:
        elements = convert("**x** 3 < 4 and <notatag> and <@lowercase>")

        assert "3 < 4" in texts(elements[0]["elements"])

    def test_html_is_not_executed(self) -> None:
        elements = convert("**x** <script>alert(1)</script>")

        assert "alert(1)" in texts(elements[0]["elements"])

    def test_empty_list_items(self) -> None:
        elements = convert("-\n-\n- real")

        assert elements[0]["type"] == "rich_text_list"
        assert [texts(item["elements"]) for item in elements[0]["elements"]] == ["real"]

    def test_very_deep_nesting(self) -> None:
        source = "\n".join("  " * level + "- level" for level in range(20))
        elements = convert(source)

        assert elements[0]["type"] == "rich_text_list"

    def test_an_html_comment_is_kept_as_text(self) -> None:
        """HTML is disabled, so a comment is literal text rather than nothing."""
        elements = convert("<!-- just a comment -->")

        assert "just a comment" in texts(elements[0]["elements"])

    def test_a_body_that_renders_to_nothing_returns_no_blocks(self) -> None:
        """The caller then falls back to sending the body as plain text."""
        assert markdown_to_blocks("```\n\n```") == []

    def test_crlf_line_endings(self) -> None:
        """A table's source lines must not carry a stray carriage return."""
        elements = convert("| a | b |\r\n|---|---|\r\n| 1 | 2 |\r\n")

        assert texts(elements[0]["elements"]) == "| a | b |\n|---|---|\n| 1 | 2 |"


class TestKitchenSink:
    """One document exercising every element, asserting the order they come out in."""

    SOURCE = """# Release notes

We shipped **the thing**, see [the MR](https://example.com/mr/1).

- first item
- second item
  - nested item
- third item

1. step one
2. step two
   1. sub step
3. step three

```sql
select count(*)
from orders;
```

> Heads up: this is a quote
>
> - with a bullet

| env | count |
|-----|-------|
| dev |     1 |

Ping <@U08GTCPJW95> in <#C0142QHKYAV|general> :white_check_mark:
"""

    def test_element_sequence(self) -> None:
        elements = convert(self.SOURCE)

        assert [(element["type"], element.get("indent")) for element in elements] == [
            ("rich_text_section", None),
            ("rich_text_list", 0),
            ("rich_text_list", 1),
            ("rich_text_list", 0),
            ("rich_text_list", 0),
            ("rich_text_list", 1),
            ("rich_text_list", 0),
            ("rich_text_preformatted", None),
            ("rich_text_quote", None),
            ("rich_text_preformatted", None),
            ("rich_text_section", None),
        ]

    def test_heading_and_paragraph_share_the_first_section(self) -> None:
        elements = convert(self.SOURCE)

        assert texts(elements[0]["elements"]) == "Release notes\n\nWe shipped the thing, see the MR."

    def test_code_block_language(self) -> None:
        elements = convert(self.SOURCE)

        assert elements[7]["language"] == "sql"

    def test_ordered_list_resumes_after_the_nested_one(self) -> None:
        elements = convert(self.SOURCE)

        assert elements[4]["style"] == "ordered"
        assert elements[6]["offset"] == 2

    def test_last_section_carries_the_slack_tokens(self) -> None:
        elements = convert(self.SOURCE)

        kinds = [element["type"] for element in elements[-1]["elements"]]
        assert "user" in kinds
        assert "channel" in kinds
        assert "emoji" in kinds
        assert elements[-1]["elements"][0] == {"type": "text", "text": "\n"}
