"""Rate limit retry handling for Slack API calls."""

from __future__ import annotations

from slack_sdk import WebClient
from slack_sdk.http_retry import RetryHandler
from slack_sdk.http_retry.builtin_handlers import RateLimitErrorRetryHandler

from .logging import get_logger

logger = get_logger(__name__)

# Default max retry count for rate limit errors
DEFAULT_MAX_RETRY_COUNT = 3


def create_rate_limit_handler(max_retry_count: int = DEFAULT_MAX_RETRY_COUNT) -> RetryHandler:
    """Create a rate limit retry handler.

    This handler automatically retries requests that receive HTTP 429 (Too Many Requests)
    responses from the Slack API. It respects the Retry-After header.

    Args:
        max_retry_count: Maximum number of retry attempts (default: 3).

    Returns:
        A RateLimitErrorRetryHandler configured with the specified max retry count.
    """
    return RateLimitErrorRetryHandler(max_retry_count=max_retry_count)


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

    client = WebClient(token=token)

    # Add retry handlers
    for handler in retry_handlers:
        client.retry_handlers.append(handler)

    logger.debug(f"Created WebClient with {len(retry_handlers)} retry handler(s)")

    return client
