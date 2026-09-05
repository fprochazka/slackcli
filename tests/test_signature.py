"""Tests for AI agent detection and the signature footer."""

from __future__ import annotations

from pathlib import Path

import pytest

from slackcli import signature as signature_module
from slackcli.signature import AGENT_ENV_VARS, GENERIC_AGENT_ENV_VARS, Signature, detect_agent, resolve_signature

ALL_AGENT_VARS = [name for _, variables in AGENT_ENV_VARS for name in variables] + GENERIC_AGENT_ENV_VARS


@pytest.fixture(autouse=True)
def no_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test in a shell that announces no agent."""
    for variable in ALL_AGENT_VARS:
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(signature_module, "DEVIN_MARKER", Path("/nonexistent-devin-marker"))


class TestDetectAgent:
    """Tests for detect_agent()."""

    def test_nothing_set(self) -> None:
        assert detect_agent() is None

    def test_claude_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDECODE", "1")

        assert detect_agent() == "Claude Code"

    def test_gemini_cli(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_CLI", "1")

        assert detect_agent() == "Gemini CLI"

    def test_an_empty_value_still_counts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Presence of the variable is the signal, whatever it holds."""
        monkeypatch.setenv("CLINE_ACTIVE", "")

        assert detect_agent() == "Cline"

    def test_generic_agent_slug(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT", "goose")

        assert detect_agent() == "Goose"

    def test_generic_slug_with_a_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Filip's own shells set this form."""
        monkeypatch.setenv("AI_AGENT", "claude-code_2-1-260_agent")

        assert detect_agent() == "Claude Code"

    def test_unknown_slug_is_title_cased(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_AGENT", "foo-bar")

        assert detect_agent() == "Foo Bar"

    def test_empty_generic_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AI_AGENT", "")

        assert detect_agent() == "an AI agent"

    def test_harness_variable_beats_the_generic_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A user may set AI_AGENT by hand, so a harness's own variable wins."""
        monkeypatch.setenv("AI_AGENT", "foo-bar")
        monkeypatch.setenv("CURSOR_AGENT", "1")

        assert detect_agent() == "Cursor"

    def test_agent_beats_ai_agent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AGENT", "amp")
        monkeypatch.setenv("AI_AGENT", "foo-bar")

        assert detect_agent() == "Amp"

    def test_devin_is_detected_by_a_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Devin sets no variable at all, only leaves a file behind."""
        monkeypatch.setattr(signature_module, "DEVIN_MARKER", tmp_path)

        assert detect_agent() == "Devin"

    def test_a_variable_a_human_also_has_is_not_a_signal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cursor's IDE terminal sets CURSOR_TRACE_ID for people too."""
        monkeypatch.setenv("CURSOR_TRACE_ID", "abc")
        monkeypatch.setenv("TERM_PROGRAM", "kiro")

        assert detect_agent() is None


class TestSignature:
    """Tests for how the footer renders."""

    def test_marketing_links_the_project(self) -> None:
        footer = Signature(agent="Claude Code", mode="marketing")

        assert footer.mrkdwn() == "— sent from <https://github.com/fprochazka/slackcli|Claude Code>"

    def test_plain_names_the_agent_only(self) -> None:
        footer = Signature(agent="Claude Code", mode="plain")

        assert footer.mrkdwn() == "— sent from Claude Code"

    def test_block_is_a_context_block(self) -> None:
        """A context block, never an attachment: attachments are cleared with previews."""
        footer = Signature(agent="Cursor", mode="plain")

        assert footer.block() == {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "— sent from Cursor"}],
        }


class TestResolveSignature:
    """Tests for resolve_signature()."""

    def test_off_never_signs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDECODE", "1")

        assert resolve_signature("off") is None

    def test_no_agent_means_no_signature(self) -> None:
        """A human running the CLI signs nothing."""
        assert resolve_signature("marketing") is None

    def test_marketing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDECODE", "1")

        assert resolve_signature("marketing") == Signature(agent="Claude Code", mode="marketing")

    def test_plain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDECODE", "1")

        assert resolve_signature("plain") == Signature(agent="Claude Code", mode="plain")
