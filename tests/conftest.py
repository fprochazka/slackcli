"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from click.testing import Result
from slack_sdk.errors import SlackApiError
from slack_sdk.web import SlackResponse
from typer.testing import CliRunner

from slackcli.cli import app

TEST_CONFIG = """
default_org = "testorg"
agent_signature = "off"

[orgs.testorg]
token = "xoxp-test-token"
"""


@pytest.fixture
def cli_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Write the test config and keep the user's own Slack environment variables out of the run.

    The conversations cache is replaced by an empty list, so a raw channel ID resolves without touching the
    user's cache directory.

    Returns:
        The path of the config file, to pass as --config.
    """
    config_path = tmp_path / "config.toml"
    config_path.write_text(TEST_CONFIG)
    monkeypatch.delenv("SLACK_ORG", raising=False)
    monkeypatch.delenv("SLACK_CONFIG", raising=False)
    monkeypatch.delenv("SLACK_AGENT_SIGNATURE", raising=False)
    monkeypatch.setattr("slackcli.client.SlackCli.get_conversations_from_cache", lambda self: [])
    return config_path


@pytest.fixture
def run_cli(cli_config_path: Path) -> Callable[..., Result]:
    """Run the slack CLI in-process against the test config, with stdout and stderr captured apart.

    Returns:
        A function taking the CLI arguments, which returns the click Result.
    """
    runner = CliRunner()

    def invoke(*args: str) -> Result:
        return runner.invoke(app, ["--config", str(cli_config_path), *args])

    return invoke


def build_slack_api_error(
    error: str,
    *,
    method: str,
    status_code: int = 200,
    headers: dict[str, Any] | None = None,
    **data: Any,
) -> SlackApiError:
    """Build the SlackApiError that slack_sdk raises for a failed call.

    Args:
        error: The Slack error code, such as "ratelimited".
        method: The API method, such as "conversations.history".
        status_code: The HTTP status of the response.
        headers: The response headers.
        **data: Extra fields of the response body, such as needed="search:read".

    Returns:
        The exception, carrying a SlackResponse like the one slack_sdk builds.
    """
    response = SlackResponse(
        client=None,
        http_verb="POST",
        api_url=f"https://slack.com/api/{method}",
        req_args={},
        data={"ok": False, "error": error, **data},
        headers=headers or {},
        status_code=status_code,
    )
    message = f"The request to the Slack API failed. (url: https://slack.com/api/{method})"
    return SlackApiError(message=message, response=response)


@pytest.fixture
def make_slack_api_error() -> Callable[..., SlackApiError]:
    """Return the builder for the SlackApiError that slack_sdk raises for a failed call."""
    return build_slack_api_error


class FailingWebClient:
    """A WebClient stand-in whose every API method raises the same SlackApiError."""

    def __init__(self, error: SlackApiError) -> None:
        self.error = error

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            raise self.error

        return call


@pytest.fixture
def fail_api_with(monkeypatch: pytest.MonkeyPatch) -> Callable[[SlackApiError], None]:
    """Make every Slack API call in the CLI raise the given error.

    Returns:
        A function taking the SlackApiError to raise.
    """

    def install(error: SlackApiError) -> None:
        monkeypatch.setattr("slackcli.client.create_web_client", lambda token, **kwargs: FailingWebClient(error))

    return install
