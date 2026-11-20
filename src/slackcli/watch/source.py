"""The Slack source of cli-watch-kit: one `conversations.history` or `conversations.replies` call per target and poll.

Targets:
- `channel <C…>`: the top-level messages of a channel, thread broadcasts included, never thread replies.
- `thread <C…>/<ts>`: the replies of one thread, never the channel's other messages.

Slack limits every Web API method on its own, per app and workspace, so each method is a budget dimension and a 429
blocks only the method that got it. Slack has no call that reads several channels, so nothing is batched.

The daemon calls slack_sdk without slackcli's retry handler: a 429 goes to the engine as `RateLimited` with its
Retry-After, and the engine waits, instead of a poll sleeping inside a worker thread.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from cli_watch_kit.budget import DimensionSpec
from cli_watch_kit.events import KindCatalog, RawEvent
from cli_watch_kit.source import (
    Params,
    PollContext,
    PollItem,
    PollOutcome,
    RateLimited,
    Source,
    SourceError,
    Target,
    Transient,
    Unauthorized,
)
from slack_sdk.errors import SlackApiError

from slackcli.watch.diff import (
    DiffResult,
    Identity,
    Snapshot,
    diff_rows,
    gap_diff,
    relevant_rows,
    ts_key,
)

MESSAGES = ("message_posted", "message_edited", "message_deleted", "messages_skipped")
CATALOG = KindCatalog(
    {
        "channel": (*MESSAGES, "channel_event"),
        "thread": MESSAGES,
    },
    groups={"messages": MESSAGES, "system": ("channel_event",)},
)

HISTORY = "history"
REPLIES = "replies"
UNLISTED_SUFFIX = "_unlisted"
# Tier 3 is 50+ calls a minute per method; half of it is left to the interactive `slack` commands of the same app,
# which the daemon does not see. An unlisted (distributed, non-Marketplace) app gets 1 call a minute.
INTERNAL_PER_MINUTE = 25.0
UNLISTED_PER_MINUTE = 1.0
DIMENSIONS = {
    HISTORY: DimensionSpec(per_minute=INTERNAL_PER_MINUTE, burst=5),
    REPLIES: DimensionSpec(per_minute=INTERNAL_PER_MINUTE, burst=5),
    HISTORY + UNLISTED_SUFFIX: DimensionSpec(per_minute=UNLISTED_PER_MINUTE, burst=1),
    REPLIES + UNLISTED_SUFFIX: DimensionSpec(per_minute=UNLISTED_PER_MINUTE, burst=1),
}
METHOD_OF = {HISTORY: "conversations.history", REPLIES: "conversations.replies"}

INTERVAL = 20.0
DEFAULT_RECHECK_WINDOW = 600.0
INTERNAL_LIMIT = 200
THREAD_LIMIT = 1000
UNLISTED_LIMIT = 15
MAX_PAGES = 5
RATE_LIMIT_CLASSES = ("internal", "unlisted")

NOT_FOUND_ERRORS = frozenset({"channel_not_found", "thread_not_found"})
NO_ACCESS_ERRORS = frozenset({"not_in_channel", "access_denied", "method_not_supported_for_channel_type"})
AUTH_ERRORS = frozenset(
    {
        "invalid_auth",
        "not_authed",
        "token_revoked",
        "token_expired",
        "account_inactive",
        "missing_scope",
        "no_permission",
    }
)

type ClientFactory = Callable[[str], Any]


def make_client(token: str) -> Any:
    """A WebClient without slackcli's rate-limit retry handler, so a 429 reaches the engine."""
    from slack_sdk import WebClient

    from slackcli.retry import create_ssl_context

    return WebClient(token=token, ssl=create_ssl_context(), timeout=30)


def thread_parts(target: Target) -> tuple[str, str]:
    channel, _, ts = target.id.partition("/")
    return channel, ts


def merged_options(item: PollItem[Snapshot]) -> tuple[float, bool]:
    """The widest recheck window any watcher asked for, and whether any of them runs on an unlisted app."""
    windows = [float(o.get("recheck_window", DEFAULT_RECHECK_WINDOW)) for o in item.options]
    unlisted = any(o.get("rate_limit") == "unlisted" for o in item.options)
    return (max(windows) if windows else DEFAULT_RECHECK_WINDOW), unlisted


def dimension_of(item: PollItem[Snapshot]) -> str:
    base = HISTORY if item.target.kind == "channel" else REPLIES
    return base + UNLISTED_SUFFIX if merged_options(item)[1] else base


def retry_after(error: SlackApiError) -> float | None:
    headers = getattr(error.response, "headers", None) or {}
    for name, value in headers.items():
        if name.lower() == "retry-after":
            raw = value[0] if isinstance(value, list) and value else value
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
    return None


def error_code(error: SlackApiError) -> str:
    try:
        return str(error.response.get("error") or "")
    except Exception:  # noqa: BLE001 - a non-JSON body
        return ""


class Ended(Exception):
    """The target is gone or out of reach: the watch ends with `reason`."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def classify(error: SlackApiError, dimension: str) -> Exception:
    """The kit error, or :class:`Ended`, for a failed Slack call."""
    code = error_code(error)
    status = getattr(error.response, "status_code", None)
    if status == 429 or code in ("ratelimited", "rate_limited"):
        method = METHOD_OF[dimension.removesuffix(UNLISTED_SUFFIX)]
        return RateLimited(f"Slack rate-limited {method}", retry_after(error), dimension=dimension)
    if code in NOT_FOUND_ERRORS:
        return Ended("not_found", f"Slack answered {code}")
    if code in NO_ACCESS_ERRORS:
        return Ended("no_access", f"Slack answered {code}")
    if code in AUTH_ERRORS:
        return Unauthorized(f"Slack refused the token: {code}")
    return Transient(f"Slack answered {code or status}")


class SlackSource(Source[Snapshot]):
    catalog = CATALOG
    dimensions = DIMENSIONS
    max_inflight = 4

    def __init__(self, client_factory: ClientFactory = make_client, clock: Callable[[], float] = time.time) -> None:
        self.client_factory = client_factory
        self.clock = clock
        self._clients: dict[str, Any] = {}
        self._identities: dict[str, tuple[str, Identity]] = {}
        self.calls: dict[str, int] = {}
        self.errors = 0

    def client(self, token: str) -> Any:
        client = self._clients.get(token)
        if client is None:
            client = self._clients[token] = self.client_factory(token)
        return client

    async def _call(self, token: str, method: str, **kwargs: Any) -> Mapping[str, Any]:
        func = getattr(self.client(token), method.replace(".", "_"))
        self.calls[method] = self.calls.get(method, 0) + 1
        try:
            response = await asyncio.to_thread(func, **kwargs)
        except SlackApiError:
            self.errors += 1
            raise
        except Exception as exc:  # noqa: BLE001 - network errors of any kind are worth another try
            self.errors += 1
            raise Transient(f"{method}: {exc}") from exc
        return response.data if hasattr(response, "data") else response  # type: ignore[no-any-return]

    async def account(self, target: Target, params: Params) -> str:
        token = str(params.get("token") or "")
        if not token:
            raise Unauthorized("no Slack token in the subscription")
        known = self._identities.get(token)
        if known is None:
            try:
                data = await self._call(token, "auth.test")
            except SlackApiError as exc:
                raise Unauthorized(f"Slack refused the token: {error_code(exc)}") from exc
            team, user = str(data.get("team_id") or ""), str(data.get("user_id") or "")
            if not team or not user:
                raise Unauthorized("Slack did not say whose token this is")
            identity = Identity(user_id=user, bot_id=str(data.get("bot_id") or "") or None)
            known = self._identities[token] = (f"{team}:{user}", identity)
        return known[0]

    def identity(self, params: Params) -> Identity:
        known = self._identities.get(str(params.get("token") or ""))
        return known[1] if known else Identity()

    def check_options(self, target: Target, options: Mapping[str, Any]) -> Mapping[str, Any]:
        out: dict[str, Any] = {}
        if "recheck_window" in options:
            window = options["recheck_window"]
            if isinstance(window, bool) or not isinstance(window, int | float) or window < 0:
                raise ValueError("recheck_window must be a number of seconds, 0 or more")
            out["recheck_window"] = float(window)
        if "rate_limit" in options:
            if options["rate_limit"] not in RATE_LIMIT_CLASSES:
                raise ValueError(f"rate_limit must be one of {', '.join(RATE_LIMIT_CLASSES)}")
            out["rate_limit"] = options["rate_limit"]
        return out

    def interval(self, target: Target, snapshot: Snapshot | None) -> float:
        return INTERVAL

    def cost(self, batch: Sequence[PollItem[Snapshot]]) -> Mapping[str, float]:
        cost: dict[str, float] = {}
        for item in batch:
            dim = dimension_of(item)
            cost[dim] = cost.get(dim, 0.0) + 1.0
        return cost

    def diff(self, target: Target, previous: Snapshot, current: Snapshot, events: frozenset[str]) -> Sequence[RawEvent]:
        if target.kind == "thread":
            channel, ts = thread_parts(target)
            found = gap_diff(previous, current, mode="thread", channel=channel, thread_ts=ts)
        else:
            found = gap_diff(previous, current, mode="channel", channel=target.id, thread_ts=None)
        return [e for e in found if e.kind in events]

    def summary(self, target: Target, snapshot: Snapshot | None) -> dict[str, Any]:
        if not snapshot:
            return {}
        key = "replies" if target.kind == "thread" else "messages_in_recheck_window"
        return {"latest_ts": snapshot.get("latest"), key: len(snapshot.get("messages") or {})}

    def status(self) -> dict[str, Any]:
        return {"calls": dict(self.calls), "errors": self.errors}

    async def poll(self, batch: Sequence[PollItem[Snapshot]], ctx: PollContext) -> Mapping[str, PollOutcome[Snapshot]]:
        outcomes: dict[str, PollOutcome[Snapshot]] = {}
        for item in batch:
            dimension = dimension_of(item)
            try:
                if item.target.kind == "thread":
                    result = await self._poll_thread(item, ctx, dimension)
                else:
                    result = await self._poll_channel(item, ctx, dimension)
            except SlackApiError as exc:
                error = classify(exc, dimension)
                if isinstance(error, Ended):
                    outcomes[item.key] = PollOutcome(item.snapshot, ended=error.reason, end_data={"error": str(error)})
                elif isinstance(error, SourceError):
                    outcomes[item.key] = PollOutcome(error=error)
                continue
            except SourceError as exc:
                outcomes[item.key] = PollOutcome(error=exc)
                continue
            events = [e for e in result.events if e.kind in item.events]
            outcomes[item.key] = PollOutcome(result.snapshot, events, ended=result.ended)
        return outcomes

    async def _page(
        self, ctx: PollContext, dimension: str, method: str, **kwargs: Any
    ) -> tuple[list[dict[str, Any]], bool, str]:
        ctx.spend({dimension: 1.0})
        data = await self._call(str(ctx.params.get("token") or ""), method, **kwargs)
        cursor = str((data.get("response_metadata") or {}).get("next_cursor") or "")
        return list(data.get("messages") or []), bool(data.get("has_more")), cursor

    async def _poll_channel(self, item: PollItem[Snapshot], ctx: PollContext, dimension: str) -> DiffResult:
        channel = item.target.id
        window, unlisted = merged_options(item)
        limit = UNLISTED_LIMIT if unlisted else INTERNAL_LIMIT
        now = Decimal(str(self.clock()))
        keep_from = now - Decimal(str(window))
        identity = self.identity(ctx.params)
        previous = item.snapshot
        latest = (previous or {}).get("latest")
        if previous is None or latest is None:
            # The baseline: the newest page, walking backward from now.
            rows, _, _ = await self._page(ctx, dimension, "conversations.history", channel=channel, limit=limit)
            picked = relevant_rows(rows, "channel")
            return diff_rows(
                None,
                picked,
                mode="channel",
                channel=channel,
                identity=identity,
                keep_from=keep_from,
                checked_from=None,
                checked_until=None,
                baseline_latest=f"{now - 60:.6f}",
            )
        oldest = min(ts_key(latest), keep_from)
        # Only `oldest` makes Slack walk forward from it, oldest page first (see docs/slack-api-overview.md).
        collected: list[dict[str, Any]] = []
        cursor = ""
        has_more = False
        for _ in range(MAX_PAGES):
            kwargs: dict[str, Any] = {"channel": channel, "oldest": f"{oldest:.6f}", "inclusive": True, "limit": limit}
            if cursor:
                kwargs["cursor"] = cursor
            rows, has_more, cursor = await self._page(ctx, dimension, "conversations.history", **kwargs)
            collected.extend(rows)
            if not has_more or not cursor:
                break
        picked = relevant_rows(collected, "channel")
        newest_unfetched: str | None = None
        checked_until: Decimal | None = None
        if has_more and cursor:
            # Too far behind to read one by one: take the channel's newest ts and report the rest as skipped.
            newest_rows, _, _ = await self._page(ctx, dimension, "conversations.history", channel=channel, limit=1)
            newest_unfetched = newest_rows[0]["ts"] if newest_rows else None
            checked_until = max((ts_key(r["ts"]) for r in collected), default=oldest)
        return diff_rows(
            previous,
            picked,
            mode="channel",
            channel=channel,
            identity=identity,
            keep_from=keep_from,
            checked_from=oldest,
            checked_until=checked_until,
            newest_unfetched=newest_unfetched,
        )

    async def _poll_thread(self, item: PollItem[Snapshot], ctx: PollContext, dimension: str) -> DiffResult:
        channel, thread_ts = thread_parts(item.target)
        _, unlisted = merged_options(item)
        limit = UNLISTED_LIMIT if unlisted else THREAD_LIMIT
        collected: list[dict[str, Any]] = []
        cursor = ""
        complete = False
        for _ in range(MAX_PAGES):
            kwargs: dict[str, Any] = {"channel": channel, "ts": thread_ts, "limit": limit}
            if cursor:
                kwargs["cursor"] = cursor
            rows, has_more, cursor = await self._page(ctx, dimension, "conversations.replies", **kwargs)
            collected.extend(rows)
            if not has_more or not cursor:
                complete = True
                break
        picked = relevant_rows(collected, "thread", thread_ts)
        checked_until = None if complete else max((ts_key(r["ts"]) for r in picked), default=Decimal(0))
        return diff_rows(
            item.snapshot,
            picked,
            mode="thread",
            channel=channel,
            identity=self.identity(ctx.params),
            keep_from=None,
            checked_from=None,
            checked_until=checked_until,
            thread_ts=thread_ts,
            baseline_latest=thread_ts,
        )
