"""Rate limit retry handling for Slack API calls."""

from __future__ import annotations

import random
import ssl
import time
from collections.abc import Callable

import certifi
from slack_sdk import WebClient
from slack_sdk.http_retry import HttpRequest, HttpResponse, RetryHandler, RetryState
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

from .errors import api_method_from_url
from .logging import error_console, get_logger

logger = get_logger(__name__)

# Default max retry count for rate limit errors
DEFAULT_MAX_RETRY_COUNT = 3


class NoticeRateLimitErrorRetryHandler(RateLimitErrorRetryHandler):
    """Retries a rate-limited call like slack_sdk's handler, and says so on stderr before every wait.

    Without the notice a rate-limited command sits silent for up to three Retry-After periods, which looks
    like a hang. The wait itself is the same as in RateLimitErrorRetryHandler: Retry-After plus up to one
    second of jitter, or one to two seconds when the header is missing.
    """

    def __init__(self, max_retry_count: int = DEFAULT_MAX_RETRY_COUNT, sleep: Callable[[float], None] = time.sleep):
        """Create the handler.

        Args:
            max_retry_count: Maximum number of retry attempts.
            sleep: The function that waits, replaceable in tests.
        """
        super().__init__(max_retry_count=max_retry_count)
        self._sleep = sleep

    def prepare_for_next_attempt(
        self,
        *,
        state: RetryState,
        request: HttpRequest,
        response: HttpResponse | None = None,
        error: Exception | None = None,
    ) -> None:
        """Print the notice, wait out the rate limit, and request the next attempt."""
        if response is None:
            raise error  # type: ignore[misc]

        state.next_attempt_requested = True
        retry_after = _retry_after_seconds(response)
        wait = (retry_after if retry_after is not None else 1) + random.random()

        method = api_method_from_url(request.url) or "unknown"
        attempt = state.current_attempt + 1
        error_console.print(
            f"Rate limited on {method}, retrying in {int(wait)}s (attempt {attempt}/{self.max_retry_count})",
            markup=False,
            highlight=False,
            soft_wrap=True,
        )

        self._sleep(wait)
        state.increment_current_attempt()


def _retry_after_seconds(response: HttpResponse) -> int | None:
    """Read the Retry-After header as whole seconds.

    Args:
        response: The rate-limited response.

    Returns:
        The number of seconds, or None when the header is missing or not a number.
    """
    for name, values in response.headers.items():
        if name.lower() == "retry-after":
            value = values[0] if isinstance(values, list) and values else values
            try:
                return int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                return None
    return None


def create_rate_limit_handler(max_retry_count: int = DEFAULT_MAX_RETRY_COUNT) -> RetryHandler:
    """Create a rate limit retry handler.

    This handler automatically retries requests that receive HTTP 429 (Too Many Requests)
    responses from the Slack API. It respects the Retry-After header and prints a notice on stderr before each wait.

    Args:
        max_retry_count: Maximum number of retry attempts (default: 3).

    Returns:
        A NoticeRateLimitErrorRetryHandler configured with the specified max retry count.
    """
    return NoticeRateLimitErrorRetryHandler(max_retry_count=max_retry_count)


def get_default_retry_handlers(max_retry_count: int = DEFAULT_MAX_RETRY_COUNT) -> list[RetryHandler]:
    """Get the default list of retry handlers.

    Args:
        max_retry_count: Maximum number of retry attempts for rate limits.

    Returns:
        A list containing the default retry handlers.
    """
    return [
        create_rate_limit_handler(max_retry_count=max_retry_count),
    ]


def create_ssl_context() -> ssl.SSLContext:
    """Create an SSL context using certifi's CA bundle.

    This ensures SSL verification works on all platforms, including macOS
    where the system certificate store may not be accessible to Python.
    """
    return ssl.create_default_context(cafile=certifi.where())


def create_web_client(
    token: str,
    retry_handlers: list[RetryHandler] | None = None,
    max_retry_count: int = DEFAULT_MAX_RETRY_COUNT,
) -> WebClient:
    """Create a WebClient with retry handlers configured.

    Args:
        token: The Slack API token.
        retry_handlers: Optional list of retry handlers. If None, default handlers are used.
        max_retry_count: Maximum number of retry attempts (used if retry_handlers is None).

    Returns:
        A configured WebClient instance.
    """
    if retry_handlers is None:
        retry_handlers = get_default_retry_handlers(max_retry_count=max_retry_count)

    client = WebClient(token=token, ssl=create_ssl_context())

    # Add retry handlers
    for handler in retry_handlers:
        client.retry_handlers.append(handler)

    logger.debug(f"Created WebClient with {len(retry_handlers)} retry handler(s)")

    return client
