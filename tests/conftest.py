"""Shared pytest fixtures."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
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
    monkeypatch.delenv("SLACK_MIN_REQUEST_INTERVAL", raising=False)
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
        self.handlers: dict[str, Callable[..., dict[str, Any]]] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def on(self, method: str, *results: dict[str, Any] | SlackApiError) -> FakeWebClient:
        """Queue the results for an API method, such as "conversations.history"."""
        self.results.setdefault(method, []).extend(results)
        return self

    def handle(self, method: str, handler: Callable[..., dict[str, Any]]) -> FakeWebClient:
        """Answer every call to an API method with ``handler(**kwargs)``, after any queued results run out."""
        self.handlers[method] = handler
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
            elif method in self.handlers:
                result = self.handlers[method](**kwargs)
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


class FakeChannelHistory:
    """conversations.history over a fixed set of messages, walking the way Slack walks.

    The rule comes from a live probe and is written down in docs/slack-api-overview.md:

    - only ``oldest`` sent, and above 0: Slack walks forward from it, each page holding the oldest ``limit``
      messages above the bound, and the next page is newer;
    - anything else (``latest`` only, both, neither, ``oldest=0``): Slack walks backward from ``latest``, or
      from now, and the next page is older;
    - every page lists its messages newest first, whichever way the walk goes;
    - ``inclusive`` decides whether a message exactly at a bound is returned.
    """

    def __init__(self, timestamps: list[str]) -> None:
        self.messages = [{"ts": ts, "text": f"message {ts}", "type": "message"} for ts in timestamps]

    def __call__(
        self,
        *,
        channel: str,
        limit: int = 100,
        oldest: str | None = None,
        latest: str | None = None,
        inclusive: bool = False,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        lower = Decimal(oldest) if oldest is not None and Decimal(oldest) > 0 else None
        upper = Decimal(latest) if latest is not None else None
        forward = lower is not None and upper is None

        def inside(ts: Decimal) -> bool:
            if lower is not None and (ts < lower or (ts == lower and not inclusive)):
                return False
            return upper is None or ts < upper or (ts == upper and inclusive)

        walk = sorted((m for m in self.messages if inside(Decimal(m["ts"]))), key=lambda m: Decimal(m["ts"]))
        if not forward:
            walk.reverse()
        start = int(cursor) if cursor else 0
        page = walk[start : start + limit]
        has_more = start + limit < len(walk)
        return {
            "messages": sorted(page, key=lambda m: Decimal(m["ts"]), reverse=True),
            "has_more": has_more,
            "response_metadata": {"next_cursor": str(start + limit) if has_more else ""},
        }


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
