"""Tests for message composition rules."""

from __future__ import annotations

import pytest

from slackcli.compose import PLAIN_TEXT_LIMIT, ComposeError, check_plain_text_length


class TestCheckPlainTextLength:
    """Tests for check_plain_text_length()."""

    def test_empty_text_passes(self) -> None:
        check_plain_text_length("")

    def test_text_at_the_limit_passes(self) -> None:
        check_plain_text_length("y" * PLAIN_TEXT_LIMIT)

    def test_text_over_the_limit_raises(self) -> None:
        with pytest.raises(ComposeError):
            check_plain_text_length("y" * (PLAIN_TEXT_LIMIT + 1))

    def test_error_names_the_length_and_the_way_out(self) -> None:
        """The message is written for an agent that has to decide what to do next."""
        with pytest.raises(ComposeError) as excinfo:
            check_plain_text_length("y" * 4500)

        message = str(excinfo.value)
        assert "Message is 4500 characters." in message
        assert "over 4000 characters" in message
        assert "Split it into several shorter messages" in message

    def test_compose_error_is_a_value_error(self) -> None:
        assert issubclass(ComposeError, ValueError)
