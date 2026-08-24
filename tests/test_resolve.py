"""Tests for URL parsing in the resolve command."""

from __future__ import annotations

import pytest

from slackcli.commands.resolve import parse_slack_url


class TestParseSlackUrl:
    """Tests for parse_slack_url()."""

    def test_basic_message_url(self) -> None:
        """Test parsing a basic message URL."""
        url = "https://example.slack.com/archives/C09D1VBRJ76/p1769432401438239"
        result = parse_slack_url(url)

        assert result.kind == "message"
        assert result.channel_id == "C09D1VBRJ76"
        assert result.message_ts == "1769432401.438239"
        assert result.thread_ts is None
        assert result.is_thread_reply is False
        assert result.workspace == "example"

    def test_thread_reply_url(self) -> None:
        """Test parsing a thread reply URL."""
        url = "https://example.slack.com/archives/C09D1VBRJ76/p1769422824936319?thread_ts=1769420875.054379&cid=C09D1VBRJ76"
        result = parse_slack_url(url)

        assert result.channel_id == "C09D1VBRJ76"
        assert result.message_ts == "1769422824.936319"
        assert result.thread_ts == "1769420875.054379"
        assert result.is_thread_reply is True
        assert result.workspace == "example"

    def test_thread_parent_url_with_thread_ts(self) -> None:
        """Test parsing a thread parent URL that includes thread_ts pointing to itself."""
        # When thread_ts equals message_ts, it's the parent, not a reply
        url = "https://example.slack.com/archives/C09D1VBRJ76/p1769420875054379?thread_ts=1769420875.054379"
        result = parse_slack_url(url)

        assert result.channel_id == "C09D1VBRJ76"
        assert result.message_ts == "1769420875.054379"
        assert result.thread_ts == "1769420875.054379"
        assert result.is_thread_reply is False  # Same ts, so it's the parent

    def test_different_workspaces(self) -> None:
        """Test parsing URLs from different workspaces."""
        urls_and_workspaces = [
            ("https://mycompany.slack.com/archives/C123/p1000000000000000", "mycompany"),
            ("https://team-name.slack.com/archives/C123/p1000000000000000", "team-name"),
            ("https://abc123.slack.com/archives/C123/p1000000000000000", "abc123"),
        ]

        for url, expected_workspace in urls_and_workspaces:
            result = parse_slack_url(url)
            assert result.workspace == expected_workspace

    def test_different_channel_ids(self) -> None:
        """Test parsing URLs with different channel ID formats."""
        channel_ids = ["C0123456789", "C09D1VBRJ76", "CABCDEFGH", "C1234567890"]

        for channel_id in channel_ids:
            url = f"https://example.slack.com/archives/{channel_id}/p1000000000000000"
            result = parse_slack_url(url)
            assert result.channel_id == channel_id

    def test_timestamp_conversion(self) -> None:
        """Test that timestamp is correctly converted from URL format."""
        test_cases = [
            ("p1769432401438239", "1769432401.438239"),
            ("p1000000000000000", "1000000000.000000"),
            ("p1234567890123456", "1234567890.123456"),
        ]

        for url_ts, expected_ts in test_cases:
            url = f"https://example.slack.com/archives/C123/{url_ts}"
            result = parse_slack_url(url)
            assert result.message_ts == expected_ts

    def test_invalid_hostname_not_slack(self) -> None:
        """Test that non-slack.com hostnames raise ValueError."""
        with pytest.raises(ValueError, match="hostname must end with slack.com"):
            parse_slack_url("https://example.com/archives/C123/p1000000000000000")

        with pytest.raises(ValueError, match="hostname must end with slack.com"):
            parse_slack_url("https://slack.example.com/archives/C123/p1000000000000000")

    def test_invalid_hostname_missing(self) -> None:
        """Test that missing hostname raises ValueError."""
        with pytest.raises(ValueError, match="hostname must end with slack.com"):
            parse_slack_url("/archives/C123/p1000000000000000")

    def test_invalid_hostname_format(self) -> None:
        """Test that invalid hostname format raises ValueError."""
        with pytest.raises(ValueError, match="Invalid Slack URL hostname format"):
            parse_slack_url("https://slack.com/archives/C123/p1000000000000000")

    def test_invalid_path_format(self) -> None:
        """Test that invalid path formats raise ValueError."""
        with pytest.raises(ValueError, match="Invalid Slack URL path format"):
            parse_slack_url("https://example.slack.com/archives/C123/invalid")

        with pytest.raises(ValueError, match="Invalid Slack URL path format"):
            parse_slack_url("https://example.slack.com/messages/C123/p1000000000000000")

        with pytest.raises(ValueError, match="Invalid Slack URL path format"):
            parse_slack_url("https://example.slack.com/")

    def test_invalid_timestamp_too_short(self) -> None:
        """Test that timestamps that are too short raise ValueError."""
        with pytest.raises(ValueError, match="Invalid timestamp in URL"):
            parse_slack_url("https://example.slack.com/archives/C123/p123456")

        with pytest.raises(ValueError, match="Invalid timestamp in URL"):
            parse_slack_url("https://example.slack.com/archives/C123/p1")

    def test_url_with_extra_query_params(self) -> None:
        """Test that extra query params don't affect parsing."""
        url = "https://example.slack.com/archives/C09D1VBRJ76/p1769422824936319?thread_ts=1769420875.054379&cid=C09D1VBRJ76&extra=ignored"
        result = parse_slack_url(url)

        assert result.channel_id == "C09D1VBRJ76"
        assert result.message_ts == "1769422824.936319"
        assert result.thread_ts == "1769420875.054379"

    def test_url_with_only_cid_query_param(self) -> None:
        """Test URL with cid but no thread_ts."""
        url = "https://example.slack.com/archives/C09D1VBRJ76/p1769432401438239?cid=C09D1VBRJ76"
        result = parse_slack_url(url)

        assert result.channel_id == "C09D1VBRJ76"
        assert result.thread_ts is None
        assert result.is_thread_reply is False

    def test_http_url(self) -> None:
        """Test that http:// URLs work (not just https://)."""
        url = "http://example.slack.com/archives/C123/p1000000000000000"
        result = parse_slack_url(url)

        assert result.channel_id == "C123"
        assert result.workspace == "example"

    def test_enterprise_grid_url(self) -> None:
        """Test enterprise grid style URLs with subdomain."""
        url = "https://company-workspace.enterprise.slack.com/archives/C123/p1000000000000000"
        result = parse_slack_url(url)

        # The subdomain extraction should get "company-workspace"
        assert result.workspace == "company-workspace"
        assert result.channel_id == "C123"


class TestParseConversationUrl:
    """Tests for conversation URLs."""

    def test_bare_archives_url(self) -> None:
        """Test parsing an archives URL without a message timestamp."""
        result = parse_slack_url("https://example.slack.com/archives/C53MDUHQR")

        assert result.kind == "conversation"
        assert result.channel_id == "C53MDUHQR"
        assert result.message_ts is None
        assert result.workspace == "example"

    def test_bare_archives_url_for_dm_and_group(self) -> None:
        """Test parsing archives URLs for DM and group conversation IDs."""
        for channel_id in ["D0123456789", "G0123456789"]:
            result = parse_slack_url(f"https://example.slack.com/archives/{channel_id}")

            assert result.kind == "conversation"
            assert result.channel_id == channel_id

    def test_bare_archives_url_with_thread_ts(self) -> None:
        """Test that thread_ts on an archives URL points at the thread root."""
        result = parse_slack_url("https://example.slack.com/archives/C123?thread_ts=1769420875.054379")

        assert result.kind == "message"
        assert result.channel_id == "C123"
        assert result.message_ts == "1769420875.054379"
        assert result.thread_ts == "1769420875.054379"
        assert result.is_thread_reply is False

    def test_legacy_messages_url(self) -> None:
        """Test parsing the legacy /messages/ conversation URL."""
        result = parse_slack_url("https://example.slack.com/messages/C53MDUHQR")

        assert result.kind == "conversation"
        assert result.channel_id == "C53MDUHQR"

    def test_trailing_slash(self) -> None:
        """Test that a trailing slash is tolerated."""
        result = parse_slack_url("https://example.slack.com/archives/C53MDUHQR/")

        assert result.kind == "conversation"
        assert result.channel_id == "C53MDUHQR"

    def test_enterprise_grid_host(self) -> None:
        """Test an enterprise grid host with a bare conversation URL."""
        result = parse_slack_url("https://rohlikgroup.enterprise.slack.com/archives/C53MDUHQR")

        assert result.kind == "conversation"
        assert result.channel_id == "C53MDUHQR"
        assert result.workspace == "rohlikgroup"


class TestParseUserUrl:
    """Tests for user URLs."""

    def test_team_url(self) -> None:
        """Test parsing a /team/ user URL."""
        result = parse_slack_url("https://example.slack.com/team/U08GTCPJW95")

        assert result.kind == "user"
        assert result.user_id == "U08GTCPJW95"
        assert result.workspace == "example"

    def test_team_url_with_w_prefix(self) -> None:
        """Test parsing an enterprise user ID that starts with W."""
        result = parse_slack_url("https://example.slack.com/team/W08GTCPJW95")

        assert result.kind == "user"
        assert result.user_id == "W08GTCPJW95"

    def test_team_url_trailing_slash(self) -> None:
        """Test that a trailing slash is tolerated."""
        result = parse_slack_url("https://example.slack.com/team/U08GTCPJW95/")

        assert result.kind == "user"
        assert result.user_id == "U08GTCPJW95"


class TestParseFileUrl:
    """Tests for file URLs."""

    def test_file_url_with_name(self) -> None:
        """Test parsing a file URL that carries the file name."""
        result = parse_slack_url("https://example.slack.com/files/U08GTCPJW95/F0123456789/report.pdf")

        assert result.kind == "file"
        assert result.file_id == "F0123456789"
        assert result.workspace == "example"

    def test_file_url_without_name(self) -> None:
        """Test parsing a file URL without the file name."""
        result = parse_slack_url("https://example.slack.com/files/U08GTCPJW95/F0123456789")

        assert result.kind == "file"
        assert result.file_id == "F0123456789"


class TestParseClientUrl:
    """Tests for app.slack.com web client URLs."""

    def test_client_conversation_url(self) -> None:
        """Test parsing a web client conversation URL."""
        result = parse_slack_url("https://app.slack.com/client/T0123456/C09D1VBRJ76")

        assert result.kind == "conversation"
        assert result.channel_id == "C09D1VBRJ76"
        assert result.workspace is None

    def test_client_thread_url(self) -> None:
        """Test parsing a web client thread URL."""
        result = parse_slack_url(
            "https://app.slack.com/client/T0123456/C09D1VBRJ76/thread/C09D1VBRJ76-1234567890.123456"
        )

        assert result.kind == "message"
        assert result.channel_id == "C09D1VBRJ76"
        assert result.message_ts == "1234567890.123456"
        assert result.thread_ts == "1234567890.123456"
        assert result.is_thread_reply is False
        assert result.workspace is None

    def test_client_thread_url_uses_thread_channel(self) -> None:
        """Test that the channel of the thread segment wins."""
        result = parse_slack_url("https://app.slack.com/client/T0123456/C111/thread/C222-1234567890.123456")

        assert result.channel_id == "C222"

    def test_client_url_trailing_slash(self) -> None:
        """Test that a trailing slash is tolerated."""
        result = parse_slack_url("https://app.slack.com/client/T0123456/C09D1VBRJ76/")

        assert result.kind == "conversation"
        assert result.channel_id == "C09D1VBRJ76"
