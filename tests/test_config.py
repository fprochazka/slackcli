"""Tests for configuration loading and org resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from slackcli.config import load_config


def write_config(tmp_path: Path, content: str) -> Path:
    """Write a config file into the temporary directory.

    Args:
        tmp_path: The pytest temporary directory.
        content: The TOML content.

    Returns:
        The path of the written file.
    """
    path = tmp_path / "config.toml"
    path.write_text(content)
    return path


GRID_CONFIG = """
[orgs.acme]
token = "xoxp-fake-1"
workspaces = ["acme-eng"]

[orgs.globex]
token = "xoxp-fake-2"
"""


class TestOrgsWithoutWorkspaces:
    """Tests for orgs that do not declare workspaces."""

    def test_loads_and_resolves_by_key(self, tmp_path: Path) -> None:
        """An org without workspaces resolves by its key."""
        config = load_config(write_config(tmp_path, '[orgs.globex]\ntoken = "xoxp-fake-2"\n'))

        org = config.get_org("globex")

        assert org.name == "globex"
        assert org.token == "xoxp-fake-2"
        assert org.workspaces == []


class TestWorkspaceResolution:
    """Tests for resolving an org by a workspace name."""

    def test_workspace_name_returns_org_entry(self, tmp_path: Path) -> None:
        """A workspace name selects the org that declares it."""
        config = load_config(write_config(tmp_path, GRID_CONFIG))

        org = config.get_org("acme-eng")

        assert org.name == "acme"

    def test_org_key_returns_same_entry(self, tmp_path: Path) -> None:
        """The org key and the workspace name select the same entry."""
        config = load_config(write_config(tmp_path, GRID_CONFIG))

        assert config.get_org("acme") is config.get_org("acme-eng")

    def test_both_names_share_the_token(self, tmp_path: Path) -> None:
        """The workspace name reuses the token of the org."""
        config = load_config(write_config(tmp_path, GRID_CONFIG))

        assert config.get_org("acme-eng").token == config.get_org("acme").token == "xoxp-fake-1"

    def test_default_org_may_name_a_workspace(self, tmp_path: Path) -> None:
        """default_org accepts a workspace name."""
        config = load_config(write_config(tmp_path, 'default_org = "acme-eng"\n' + GRID_CONFIG))

        assert config.get_org().name == "acme"


class TestCollisions:
    """Tests for the load time collision checks."""

    def test_workspace_equal_to_another_org_key(self, tmp_path: Path) -> None:
        """A workspace name must not repeat the key of another org."""
        content = """
[orgs.acme]
token = "xoxp-fake-1"
workspaces = ["globex"]

[orgs.globex]
token = "xoxp-fake-2"
"""
        with pytest.raises(ValueError, match="collides with the org named 'globex'"):
            load_config(write_config(tmp_path, content))

    def test_workspace_equal_to_own_org_key(self, tmp_path: Path) -> None:
        """A workspace name must not repeat the key of its own org."""
        content = """
[orgs.acme]
token = "xoxp-fake-1"
workspaces = ["acme"]
"""
        with pytest.raises(ValueError, match="repeats the org name"):
            load_config(write_config(tmp_path, content))

    def test_same_workspace_under_two_orgs(self, tmp_path: Path) -> None:
        """Two orgs must not claim the same workspace name."""
        content = """
[orgs.acme]
token = "xoxp-fake-1"
workspaces = ["shared"]

[orgs.globex]
token = "xoxp-fake-2"
workspaces = ["shared"]
"""
        with pytest.raises(ValueError, match="belongs to org 'acme' and to org 'globex'"):
            load_config(write_config(tmp_path, content))

    def test_workspace_listed_twice_in_one_org(self, tmp_path: Path) -> None:
        """One org must not list the same workspace name twice."""
        content = """
[orgs.acme]
token = "xoxp-fake-1"
workspaces = ["skillz", "skillz"]
"""
        with pytest.raises(ValueError, match="listed twice for org 'acme'"):
            load_config(write_config(tmp_path, content))


class TestWorkspacesType:
    """Tests for the type of the workspaces value."""

    def test_not_a_list(self, tmp_path: Path) -> None:
        """A scalar workspaces value is rejected."""
        content = """
[orgs.acme]
token = "xoxp-fake-1"
workspaces = "acme-eng"
"""
        with pytest.raises(ValueError, match="Invalid 'workspaces' for org 'acme'"):
            load_config(write_config(tmp_path, content))

    def test_list_with_non_string(self, tmp_path: Path) -> None:
        """A list holding a non string is rejected."""
        content = """
[orgs.acme]
token = "xoxp-fake-1"
workspaces = ["acme-eng", 42]
"""
        with pytest.raises(ValueError, match="Invalid 'workspaces' for org 'acme'"):
            load_config(write_config(tmp_path, content))


class TestNotFoundMessage:
    """Tests for the message of an unknown org name."""

    def test_message_lists_workspace_names(self, tmp_path: Path) -> None:
        """The available list names the workspaces of each org."""
        config = load_config(write_config(tmp_path, GRID_CONFIG))

        with pytest.raises(ValueError) as excinfo:
            config.get_org("unknown")

        message = str(excinfo.value)
        assert "Organization 'unknown' not found." in message
        assert "acme (workspaces: acme-eng)" in message
        assert "globex" in message
