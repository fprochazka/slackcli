"""Tests for the rate-limit retry handler and its stderr notice."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from slack_sdk import WebClient
from slack_sdk.http_retry import HttpRequest, HttpResponse, RetryState

from slackcli.client import SlackCli
from slackcli.config import Config, OrgConfig
from slackcli.context import Context
from slackcli.retry import NoticeRateLimitErrorRetryHandler, PacedWebClient, RequestPacer, create_web_client


class FakeSleep:
    """Records every wait instead of sleeping."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def rate_limited_response(headers: dict[str, str] | None = None) -> HttpResponse:
    return HttpResponse(status_code=429, headers=headers if headers is not None else {"Retry-After": "30"})


def request_for(method: str) -> HttpRequest:
    return HttpRequest(method="POST", url=f"https://slack.com/api/{method}", headers={})


@pytest.mark.parametrize("header_name", ["Retry-After", "retry-after"])
def test_notice_names_method_wait_and_attempt(capsys: pytest.CaptureFixture[str], header_name: str) -> None:
    sleep = FakeSleep()
    handler = NoticeRateLimitErrorRetryHandler(sleep=sleep)
    state = RetryState()

    handler.prepare_for_next_attempt(
        state=state,
        request=request_for("conversations.history"),
        response=rate_limited_response({header_name: "30"}),
    )

    captured = capsys.readouterr()
    assert captured.err == "Rate limited on conversations.history, retrying in 30s (attempt 1/3)\n"
    assert captured.out == ""
    assert len(sleep.waits) == 1
    assert 30 <= sleep.waits[0] < 31
    assert state.next_attempt_requested is True
    assert state.current_attempt == 1


def test_missing_retry_after_waits_about_one_second(capsys: pytest.CaptureFixture[str]) -> None:
    sleep = FakeSleep()
    handler = NoticeRateLimitErrorRetryHandler(sleep=sleep)

    handler.prepare_for_next_attempt(
        state=RetryState(),
        request=request_for("users.list"),
        response=rate_limited_response({}),
    )

    assert capsys.readouterr().err == "Rate limited on users.list, retrying in 1s (attempt 1/3)\n"
    assert 1 <= sleep.waits[0] < 2


def test_attempts_are_counted_up_to_the_limit(capsys: pytest.CaptureFixture[str]) -> None:
    sleep = FakeSleep()
    handler = NoticeRateLimitErrorRetryHandler(sleep=sleep)
    state = RetryState()
    request = request_for("conversations.replies")
    response = rate_limited_response({"Retry-After": "2"})

    while handler.can_retry(state=state, request=request, response=response):
        handler.prepare_for_next_attempt(state=state, request=request, response=response)

    assert capsys.readouterr().err.splitlines() == [
        "Rate limited on conversations.replies, retrying in 2s (attempt 1/3)",
        "Rate limited on conversations.replies, retrying in 2s (attempt 2/3)",
        "Rate limited on conversations.replies, retrying in 2s (attempt 3/3)",
    ]
    assert len(sleep.waits) == 3


def test_create_web_client_installs_the_notice_handler() -> None:
    client = create_web_client(token="xoxp-test-token")

    assert any(isinstance(handler, NoticeRateLimitErrorRetryHandler) for handler in client.retry_handlers)


class RateLimitOnceServer(BaseHTTPRequestHandler):
    """Answers the first request with a 429 and every later one with an ok response."""

    calls = 0

    def do_POST(self) -> None:  # noqa: N802 - name required by BaseHTTPRequestHandler
        type(self).calls += 1
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if type(self).calls == 1:
            body = json.dumps({"ok": False, "error": "ratelimited"}).encode()
            self.send_response(429)
            self.send_header("Retry-After", "4")
        else:
            body = json.dumps({"ok": True, "channels": []}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - signature of the base class
        pass


@pytest.fixture
def rate_limit_once_server() -> Iterator[str]:
    RateLimitOnceServer.calls = 0
    server = HTTPServer(("127.0.0.1", 0), RateLimitOnceServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/api/"
    finally:
        server.shutdown()
        server.server_close()


def test_web_client_prints_the_notice_and_retries(
    rate_limit_once_server: str, capsys: pytest.CaptureFixture[str]
) -> None:
    sleep = FakeSleep()
    client = WebClient(
        token="xoxp-test-token",
        base_url=rate_limit_once_server,
        retry_handlers=[NoticeRateLimitErrorRetryHandler(sleep=sleep)],
    )

    response = client.conversations_list()

    assert response["ok"] is True
    assert RateLimitOnceServer.calls == 2
    assert capsys.readouterr().err == "Rate limited on conversations.list, retrying in 4s (attempt 1/3)\n"
    assert len(sleep.waits) == 1
    assert 4 <= sleep.waits[0] < 5


class FakeClock:
    """A monotonic clock that only moves when the code under test sleeps, or when the test moves it."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_pacer_with_zero_interval_never_waits() -> None:
    clock = FakeClock()
    pacer = RequestPacer(0, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        pacer.wait()
        pacer.mark()

    assert clock.sleeps == []


def test_pacer_waits_out_the_rest_of_the_interval_from_the_end_of_the_last_call() -> None:
    clock = FakeClock()
    pacer = RequestPacer(1.0, clock=clock, sleep=clock.sleep)

    pacer.wait()  # the first call never waits
    clock.now += 0.3  # the call itself takes 0.3s
    pacer.mark()
    clock.now += 0.2  # the caller works for 0.2s before the next call
    pacer.wait()

    assert clock.sleeps == [pytest.approx(0.8)]

    pacer.mark()
    clock.now += 1.5  # more than the interval has already passed
    pacer.wait()

    assert clock.sleeps == [pytest.approx(0.8)]


def test_create_web_client_paces_with_the_configured_interval() -> None:
    client = create_web_client(token="xoxp-test-token", min_request_interval=0.5)

    assert isinstance(client, PacedWebClient)
    assert client.pacer.min_interval == 0.5


def test_rate_limit_wait_and_pacing_do_not_add_up(
    rate_limit_once_server: str, capsys: pytest.CaptureFixture[str]
) -> None:
    clock = FakeClock()
    pacer = RequestPacer(1.0, clock=clock, sleep=clock.sleep)
    client = PacedWebClient(
        token="xoxp-test-token",
        base_url=rate_limit_once_server,
        pacer=pacer,
        retry_handlers=[NoticeRateLimitErrorRetryHandler(sleep=clock.sleep)],
    )

    client.conversations_list()  # 429 with Retry-After 4, then 200
    client.conversations_list()  # 200

    # One rate-limit wait of 4-5s with no pacing on top of it, then one full interval before the next call
    assert len(clock.sleeps) == 2
    assert 4 <= clock.sleeps[0] < 5
    assert clock.sleeps[1] == pytest.approx(1.0)
    assert RateLimitOnceServer.calls == 3
    capsys.readouterr()


def test_slack_client_gets_the_interval_from_the_config(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[dict] = []
    monkeypatch.setattr("slackcli.client.create_web_client", lambda **kwargs: created.append(kwargs) or object())
    config = Config(orgs={"acme": OrgConfig(name="acme", token="xoxp-test-token")}, default_org="acme")
    config.min_request_interval = 0.25

    slack = Context(config=config).get_slack_client()
    slack.client  # noqa: B018 - creates the WebClient

    assert isinstance(slack, SlackCli)
    assert created == [{"token": "xoxp-test-token", "min_request_interval": 0.25}]
