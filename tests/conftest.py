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


@pytest.fixture(autouse=True)
def isolated_cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the slackcli cache at a temporary directory, so no test reads or writes the user's own cache.

    Returns:
        The cache directory; the cache of the test org is its "testorg" subdirectory.
    """
    cache_dir = tmp_path / "cache"
    monkeypatch.setattr("slackcli.cache.DEFAULT_CACHE_DIR", cache_dir)
    return cache_dir


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


class FakeWebClient:
    """A stand-in for slack_sdk's WebClient that answers each API method from a script.

    Script a method with ``on("users.list", page1, page2, error)``. Every call takes the next result: a dict
    becomes an ok response (``"ok": True`` is added unless the dict sets it), a SlackApiError is raised. A call
    with no result left raises ``default_error`` when one is set, and fails the test otherwise. Every call is
    recorded in ``calls`` as ``(method, kwargs)``.
    """

    def __init__(self, default_error: SlackApiError | None = None) -> None:
        self.default_error = default_error
        self.results: dict[str, list[dict[str, Any] | SlackApiError]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def on(self, method: str, *results: dict[str, Any] | SlackApiError) -> FakeWebClient:
        """Queue the results for an API method, such as "conversations.history"."""
        self.results.setdefault(method, []).extend(results)
        return self

    def calls_to(self, method: str) -> list[dict[str, Any]]:
        """Return the kwargs of every call made to an API method."""
        return [kwargs for name, kwargs in self.calls if name == method]

    def __getattr__(self, name: str) -> Callable[..., Any]:
        if name.startswith("_"):
            raise AttributeError(name)
        # users_list -> users.list, chat_postMessage -> chat.postMessage
        method = name.replace("_", ".", 1)

        def call(*args: Any, **kwargs: Any) -> SlackResponse:
            self.calls.append((method, kwargs))
            queue = self.results.get(method)
            if queue:
                result = queue.pop(0)
            elif self.default_error is not None:
                result = self.default_error
            else:
                raise AssertionError(f"Unexpected Slack API call: {method} {kwargs}")
            if isinstance(result, SlackApiError):
                raise result
            return SlackResponse(
                client=None,
                http_verb="POST",
                api_url=f"https://slack.com/api/{method}",
                req_args=kwargs,
                data={"ok": True, **result},
                headers={},
                status_code=200,
            )

        return call


@pytest.fixture
def fake_slack(monkeypatch: pytest.MonkeyPatch) -> FakeWebClient:
    """Install a FakeWebClient as the client of every SlackCli the CLI creates.

    Returns:
        The fake, to script with ``on`` and to inspect through ``calls``.
    """
    fake = FakeWebClient()
    monkeypatch.setattr("slackcli.client.create_web_client", lambda token, **kwargs: fake)
    return fake


@pytest.fixture
def fail_api_with(monkeypatch: pytest.MonkeyPatch) -> Callable[[SlackApiError], None]:
    """Make every Slack API call in the CLI raise the given error.

    Returns:
        A function taking the SlackApiError to raise.
    """

    def install(error: SlackApiError) -> None:
        fake = FakeWebClient(default_error=error)
        monkeypatch.setattr("slackcli.client.create_web_client", lambda token, **kwargs: fake)

    return install
