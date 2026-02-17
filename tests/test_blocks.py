"""Tests for Block Kit rendering."""

from __future__ import annotations

from slackcli.blocks import (
    get_message_text,
    render_attachment,
    render_section_block,
)

USERS: dict[str, str] = {"U001": "alice", "U002": "bob"}
CHANNELS: dict[str, str] = {"C001": "general", "C002": "random"}


class TestGetMessageText:
    """Tests for get_message_text() combining blocks, attachments, and text."""

    def test_blocks_only(self) -> None:
        message = {
            "text": "fallback",
            "blocks": [
                {
                    "type": "rich_text",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "Hello from blocks"}],
                        }
                    ],
                }
            ],
        }
        result = get_message_text(message, USERS, CHANNELS)
        assert result == "Hello from blocks"

    def test_attachments_only(self) -> None:
        message = {
            "text": "",
            "attachments": [
                {
                    "text": "Alert triggered",
                    "title": "Monitor Alert",
                    "title_link": "https://example.com/alert/123",
                }
            ],
        }
        result = get_message_text(message, USERS, CHANNELS)
        assert "Monitor Alert" in result
        assert "Alert triggered" in result

    def test_text_fallback(self) -> None:
        message = {"text": "Simple text message"}
        result = get_message_text(message, USERS, CHANNELS)
        assert result == "Simple text message"

    def test_empty_message(self) -> None:
        message = {"text": ""}
        result = get_message_text(message, USERS, CHANNELS)
        assert result == ""

    def test_blocks_and_attachments_combined(self) -> None:
        """Bot messages often have a minimal title in blocks and details in attachments."""
        message = {
            "text": "Status Update:",
            "blocks": [
                {
                    "type": "rich_text",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [
                                {"type": "emoji", "name": "test_tube"},
                                {"type": "text", "text": " Status Update:"},
                            ],
                        }
                    ],
                }
            ],
            "attachments": [
                {
                    "text": "Pipeline: #12345\nEnvironment: staging\nResult: All tests passed",
                    "color": "36a64f",
                }
            ],
        }
        result = get_message_text(message, USERS, CHANNELS)
        assert ":test_tube: Status Update:" in result
        assert "Pipeline: #12345" in result
        assert "All tests passed" in result

    def test_blocks_present_but_empty_still_shows_attachments(self) -> None:
        """If blocks render to empty, attachments should still be shown."""
        message = {
            "text": "",
            "blocks": [{"type": "divider"}],
            "attachments": [{"text": "Important details here"}],
        }
        result = get_message_text(message, USERS, CHANNELS)
        assert "Important details here" in result

    def test_text_fallback_not_used_when_blocks_present(self) -> None:
        """The text field is a fallback — should not appear when blocks render content."""
        message = {
            "text": "This is the fallback text",
            "blocks": [
                {
                    "type": "rich_text",
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [{"type": "text", "text": "Rich content"}],
                        }
                    ],
                }
            ],
        }
        result = get_message_text(message, USERS, CHANNELS)
        assert "Rich content" in result
        assert "fallback" not in result


class TestRenderSectionBlock:
    """Tests for render_section_block() including accessory rendering."""

    def test_text_only(self) -> None:
        block = {"type": "section", "text": {"type": "mrkdwn", "text": "Hello world"}}
        assert render_section_block(block) == "Hello world"

    def test_text_with_button_accessory(self) -> None:
        """Section blocks can have an inline button with a URL."""
        block = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": ">*Fix the login bug*"},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": ":link: PROJ-1234"},
                "url": "https://example.com/issue/PROJ-1234",
            },
        }
        result = render_section_block(block)
        assert ">*Fix the login bug*" in result
        assert "[:link: PROJ-1234]" in result
        assert "https://example.com/issue/PROJ-1234" in result

    def test_text_with_button_accessory_no_url(self) -> None:
        block = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "Pick an action"},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "Do Something"},
            },
        }
        result = render_section_block(block)
        assert "Pick an action" in result
        assert "[Do Something]" in result

    def test_text_with_image_accessory(self) -> None:
        block = {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "Check the dashboard"},
            "accessory": {
                "type": "image",
                "image_url": "https://example.com/chart.png",
                "alt_text": "CPU usage chart",
            },
        }
        result = render_section_block(block)
        assert "Check the dashboard" in result
        assert "[Image: CPU usage chart]" in result

    def test_no_accessory(self) -> None:
        block = {"type": "section", "text": {"type": "mrkdwn", "text": "Just text"}}
        assert render_section_block(block) == "Just text"

    def test_empty_text(self) -> None:
        block = {"type": "section", "text": {"type": "mrkdwn", "text": ""}}
        assert render_section_block(block) == ""


class TestRenderAttachment:
    """Tests for render_attachment() with image_url and legacy actions."""

    def test_basic_attachment(self) -> None:
        attachment = {
            "title": "Alert Fired",
            "title_link": "https://example.com/alert/1",
            "text": "CPU usage exceeded 90%",
        }
        result = render_attachment(attachment, USERS, CHANNELS)
        assert "Alert Fired (https://example.com/alert/1)" in result
        assert "CPU usage exceeded 90%" in result

    def test_attachment_with_image_url(self) -> None:
        """Monitoring tools often include graph snapshots as image_url."""
        attachment = {
            "title": "Metric Alert",
            "text": "Value: 95.2%",
            "image_url": "https://example.com/snapshots/graph-abc123.png",
            "image_width": 800,
            "image_height": 400,
        }
        result = render_attachment(attachment, USERS, CHANNELS)
        assert "Metric Alert" in result
        assert "Value: 95.2%" in result
        assert "[Image] (https://example.com/snapshots/graph-abc123.png)" in result

    def test_attachment_with_legacy_actions(self) -> None:
        """Legacy-format action buttons at the attachment level."""
        attachment = {
            "text": "Server is down",
            "actions": [
                {"type": "button", "text": "Acknowledge"},
                {"type": "button", "text": "Create Incident"},
                {"type": "button", "text": "Mute"},
            ],
        }
        result = render_attachment(attachment, USERS, CHANNELS)
        assert "Server is down" in result
        assert "[Acknowledge] [Create Incident] [Mute]" in result

    def test_attachment_with_fields(self) -> None:
        attachment = {
            "text": "Deployment complete",
            "fields": [
                {"title": "Service", "value": "api-gateway"},
                {"title": "Version", "value": "2.4.1"},
            ],
        }
        result = render_attachment(attachment, USERS, CHANNELS)
        assert "Service: api-gateway" in result
        assert "Version: 2.4.1" in result

    def test_attachment_with_blocks_inside(self) -> None:
        """App unfurls and bot messages put Block Kit blocks inside attachments."""
        attachment = {
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*PR #42* - Fix login timeout"},
                },
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "*Repo:* backend-api"}],
                },
                {
                    "type": "actions",
                    "elements": [
                        {"type": "button", "text": {"type": "plain_text", "text": "View PR"}},
                    ],
                },
            ],
            "fallback": "PR #42 - Fix login timeout",
        }
        result = render_attachment(attachment, USERS, CHANNELS)
        assert "*PR #42* - Fix login timeout" in result
        assert "*Repo:* backend-api" in result
        assert "[View PR]" in result

    def test_attachment_with_everything(self) -> None:
        """Full attachment with title, text, fields, image, and actions."""
        attachment = {
            "title": "Monitor Triggered",
            "title_link": "https://example.com/monitor/99",
            "text": "Pod restarted due to OOM",
            "fields": [
                {"title": "Tags", "value": "service:api, env:prod"},
                {"title": "Notified", "value": "@oncall-team"},
            ],
            "image_url": "https://example.com/snapshots/memory-graph.png",
            "actions": [
                {"type": "button", "text": "Mute"},
                {"type": "button", "text": "Escalate"},
            ],
        }
        result = render_attachment(attachment, USERS, CHANNELS)
        lines = result.split("\n")
        assert "Monitor Triggered (https://example.com/monitor/99)" in lines[0]
        assert "Pod restarted due to OOM" in result
        assert "Tags: service:api, env:prod" in result
        assert "[Image] (https://example.com/snapshots/memory-graph.png)" in result
        assert "[Mute] [Escalate]" in result
