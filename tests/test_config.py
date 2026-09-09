"""Tests for configuration loading and org resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from slackcli.config import AGENT_SIGNATURE_ENV_VAR, load_config


@pytest.fixture(autouse=True)
def clear_agent_signature_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep an ambient SLACK_AGENT_SIGNATURE out of every test in this module."""
    monkeypatch.delenv(AGENT_SIGNATURE_ENV_VAR, raising=False)


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


class TestAgentSignature:
    """Tests for the top-level 'agent_signature' key."""

    def test_defaults_to_marketing(self, tmp_path: Path) -> None:
        config = load_config(write_config(tmp_path, '[orgs.globex]\ntoken = "xoxp-fake-2"\n'))

        assert config.agent_signature == "marketing"

    def test_each_mode_is_accepted(self, tmp_path: Path) -> None:
        for mode in ("off", "plain", "marketing"):
            content = f'agent_signature = "{mode}"\n[orgs.globex]\ntoken = "xoxp-fake-2"\n'
            config = load_config(write_config(tmp_path, content))

            assert config.agent_signature == mode

    def test_case_and_spacing_are_forgiven(self, tmp_path: Path) -> None:
        content = 'agent_signature = "  Marketing "\n[orgs.globex]\ntoken = "xoxp-fake-2"\n'

        config = load_config(write_config(tmp_path, content))

        assert config.agent_signature == "marketing"

    def test_invalid_value_is_refused(self, tmp_path: Path) -> None:
        content = 'agent_signature = "loud"\n[orgs.globex]\ntoken = "xoxp-fake-2"\n'

        with pytest.raises(ValueError) as excinfo:
            load_config(write_config(tmp_path, content))

        assert str(excinfo.value) == "Invalid 'agent_signature': expected one of off, plain, marketing"

    def test_a_value_that_is_not_a_string_is_refused(self, tmp_path: Path) -> None:
        content = 'agent_signature = 3\n[orgs.globex]\ntoken = "xoxp-fake-2"\n'

        with pytest.raises(ValueError):
            load_config(write_config(tmp_path, content))


class TestAgentSignatureEnvVar:
    """Tests for the SLACK_AGENT_SIGNATURE environment variable."""

    def test_env_var_overrides_the_config_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The variable wins over the key in the file."""
        monkeypatch.setenv(AGENT_SIGNATURE_ENV_VAR, "off")
        content = 'agent_signature = "marketing"\n[orgs.globex]\ntoken = "xoxp-fake-2"\n'

        config = load_config(write_config(tmp_path, content))

        assert config.agent_signature == "off"

    def test_env_var_applies_without_the_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The variable applies when the file names no mode."""
        monkeypatch.setenv(AGENT_SIGNATURE_ENV_VAR, "plain")

        config = load_config(write_config(tmp_path, '[orgs.globex]\ntoken = "xoxp-fake-2"\n'))

        assert config.agent_signature == "plain"

    def test_case_and_spacing_are_forgiven(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The value is trimmed and lower-cased like the file value is."""
        monkeypatch.setenv(AGENT_SIGNATURE_ENV_VAR, "  PLAIN  ")

        config = load_config(write_config(tmp_path, '[orgs.globex]\ntoken = "xoxp-fake-2"\n'))

        assert config.agent_signature == "plain"

    def test_invalid_value_is_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unknown mode is refused, and the message names the variable."""
        monkeypatch.setenv(AGENT_SIGNATURE_ENV_VAR, "loud")

        with pytest.raises(ValueError) as excinfo:
            load_config(write_config(tmp_path, '[orgs.globex]\ntoken = "xoxp-fake-2"\n'))

        assert str(excinfo.value) == "Invalid SLACK_AGENT_SIGNATURE: expected one of off, plain, marketing"

    def test_empty_value_leaves_the_file_in_charge(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """An exported but empty variable is not an error and does not override."""
        monkeypatch.setenv(AGENT_SIGNATURE_ENV_VAR, "")
        content = 'agent_signature = "plain"\n[orgs.globex]\ntoken = "xoxp-fake-2"\n'

        config = load_config(write_config(tmp_path, content))

        assert config.agent_signature == "plain"

    def test_blank_value_falls_back_to_the_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A whitespace-only variable counts as absent, so the default stands."""
        monkeypatch.setenv(AGENT_SIGNATURE_ENV_VAR, "   ")

        config = load_config(write_config(tmp_path, '[orgs.globex]\ntoken = "xoxp-fake-2"\n'))

        assert config.agent_signature == "marketing"
