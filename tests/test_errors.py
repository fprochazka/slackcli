"""Tests for how the CLI reports a failed Slack API call: stderr text and exit code."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import Result
from slack_sdk.errors import SlackApiError

from slackcli import cli as cli_module
from slackcli.errors import EXIT_RATE_LIMITED, get_api_method, get_retry_after

RunCli = Callable[..., Result]
FailApiWith = Callable[[SlackApiError], None]
MakeError = Callable[..., SlackApiError]


@pytest.mark.parametrize("header_name", ["Retry-After", "retry-after", "RETRY-AFTER"])
@pytest.mark.parametrize("error_code", ["ratelimited", "rate_limited"])
def test_rate_limit_exits_75_with_retry_after_and_method(
    run_cli: RunCli, fail_api_with: FailApiWith, header_name: str, error_code: str, make_slack_api_error: MakeError
) -> None:
    fail_api_with(make_slack_api_error(error_code, method="pins.list", status_code=429, headers={header_name: "30"}))

    result = run_cli("pins", "list", "C0123456789")

    assert result.exit_code == EXIT_RATE_LIMITED
    assert f"Slack API error: {error_code}" in result.stderr
    assert "Retry-After: 30s (method: pins.list)\n" in result.stderr
    assert "Hint: Slack kept rate-limiting this call" in result.stderr
    assert "Retry-After" not in result.stdout


def test_rate_limit_without_retry_after_header_says_unknown(
    run_cli: RunCli, fail_api_with: FailApiWith, make_slack_api_error: MakeError
) -> None:
    fail_api_with(make_slack_api_error("ratelimited", method="conversations.history", status_code=429))

    result = run_cli("messages", "list", "C0123456789")

    assert result.exit_code == EXIT_RATE_LIMITED
    assert "Retry-After: unknown (method: conversations.history)\n" in result.stderr


def test_rate_limit_under_json_leaves_stdout_empty(
    run_cli: RunCli, fail_api_with: FailApiWith, make_slack_api_error: MakeError
) -> None:
    fail_api_with(
        make_slack_api_error(
            "ratelimited", method="conversations.history", status_code=429, headers={"Retry-After": "5"}
        )
    )

    result = run_cli("messages", "list", "C0123456789", "--json")

    assert result.exit_code == EXIT_RATE_LIMITED
    assert result.stdout == ""
    assert "Retry-After: 5s (method: conversations.history)" in result.stderr


def test_rate_limit_in_search_exits_75(
    run_cli: RunCli, fail_api_with: FailApiWith, make_slack_api_error: MakeError
) -> None:
    fail_api_with(
        make_slack_api_error("ratelimited", method="search.messages", status_code=429, headers={"Retry-After": "12"})
    )

    result = run_cli("search", "messages", "quarterly report", "--json")

    assert result.exit_code == EXIT_RATE_LIMITED
    assert result.stdout == ""
    assert "Retry-After: 12s (method: search.messages)" in result.stderr


def test_other_api_error_exits_1_without_retry_after_line(
    run_cli: RunCli, fail_api_with: FailApiWith, make_slack_api_error: MakeError
) -> None:
    fail_api_with(make_slack_api_error("channel_not_found", method="pins.list"))

    result = run_cli("pins", "list", "C0123456789", "--json")

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Slack API error: channel_not_found" in result.stderr
    assert "Hint: Channel not found" in result.stderr
    assert "Retry-After" not in result.stderr


def test_search_missing_scope_keeps_its_own_message(
    run_cli: RunCli, fail_api_with: FailApiWith, make_slack_api_error: MakeError
) -> None:
    fail_api_with(make_slack_api_error("missing_scope", method="search.messages", needed="search:read"))

    result = run_cli("search", "messages", "quarterly report")

    assert result.exit_code == 1
    assert "Missing required OAuth scope: search:read" in result.stderr


def test_slack_error_escaping_every_handler_is_reported_by_the_entry_point(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    cli_config_path: Path,
    make_slack_api_error: MakeError,
) -> None:
    error = make_slack_api_error("ratelimited", method="users.info", status_code=429, headers={"retry-after": "7"})

    def get_slack_client(self: object) -> None:
        raise error

    # Every command creates its client before its own try block, so this error reaches no command handler.
    monkeypatch.setattr("slackcli.context.Context.get_slack_client", get_slack_client)
    monkeypatch.setattr("sys.argv", ["slack", "--config", str(cli_config_path), "pins", "list", "C0123456789"])

    with pytest.raises(SystemExit) as exc_info:
        cli_module.cli()

    assert exc_info.value.code == EXIT_RATE_LIMITED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Slack API error: ratelimited" in captured.err
    assert "Retry-After: 7s (method: users.info)" in captured.err


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({"Retry-After": "30"}, "30"),
        ({"retry-after": "30"}, "30"),
        ({"Retry-After": ["30"]}, "30"),
        ({"Content-Type": "application/json"}, None),
        ({}, None),
    ],
)
def test_get_retry_after(headers: dict, expected: str | None, make_slack_api_error: MakeError) -> None:
    error = make_slack_api_error("ratelimited", method="conversations.history", status_code=429, headers=headers)

    assert get_retry_after(error) == expected


def test_get_api_method(make_slack_api_error: MakeError) -> None:
    error = make_slack_api_error("ratelimited", method="conversations.replies")

    assert get_api_method(error) == "conversations.replies"


def test_get_api_method_without_url() -> None:
    error = SlackApiError(message="failed", response={"ok": False, "error": "ratelimited"})

    assert get_api_method(error) is None
    assert get_retry_after(error) is None
