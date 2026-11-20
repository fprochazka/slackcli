"""The pure diff of `slack watch`: Slack rows against the previous snapshot, into events and the next snapshot.

A snapshot is `{"latest": <ts>, "messages": {<ts>: {"author", "bot_id", "text", "edited", "subtype"}}}`. `latest` is
the newest ts seen, the cursor a channel pages forward from. `messages` holds what edits and deletions are checked
against: the rows inside the recheck window for a channel, every reply for a thread. Each entry keeps the text, so a
deletion can say what was deleted; Slack keeps no earlier text of an edited message, so an edit carries none.

Events carry Slack's raw row in `message`; the watcher turns it into the object `messages list --json` prints.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from cli_watch_kit.events import RawEvent

type Snapshot = dict[str, Any]
type Mode = Literal["channel", "thread"]

CATCH_UP_CAP = 50
"""More new messages than this in one poll are reported as one `messages_skipped`, not one by one."""

SYSTEM_SUBTYPES = frozenset(
    {
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "channel_unarchive",
        "channel_convert_to_private",
        "channel_convert_to_public",
        "channel_posting_permissions",
        "pinned_item",
        "unpinned_item",
        "group_join",
        "group_leave",
        "group_topic",
        "group_purpose",
        "group_name",
        "group_unarchive",
    }
)
"""Rows about the channel rather than a message in it: reported as `channel_event`, never checked for edits."""

ARCHIVE_SUBTYPES = frozenset({"channel_archive", "group_archive"})
TOMBSTONE = "tombstone"
"""A deleted thread parent that still has replies: Slack keeps the row with this subtype and empty text."""


@dataclass(frozen=True)
class Identity:
    """Who the token belongs to, to mark the watcher's own messages."""

    user_id: str | None = None
    bot_id: str | None = None

    def owns(self, row: Mapping[str, Any]) -> bool:
        if self.user_id and row.get("user") == self.user_id:
            return True
        return bool(self.bot_id and row.get("bot_id") == self.bot_id)


@dataclass
class DiffResult:
    snapshot: Snapshot
    events: list[RawEvent] = field(default_factory=list)
    ended: str | None = None


def ts_key(ts: str) -> Decimal:
    try:
        return Decimal(ts)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def entry(row: Mapping[str, Any]) -> dict[str, Any]:
    """What the snapshot keeps of a row."""
    return {
        "author": row.get("user") or None,
        "bot_id": row.get("bot_id") or None,
        "text": row.get("text") or "",
        "edited": (row.get("edited") or {}).get("ts"),
        "subtype": row.get("subtype") or None,
    }


def is_reply(row: Mapping[str, Any]) -> bool:
    """A thread reply that is not also posted to the channel."""
    thread_ts = row.get("thread_ts")
    return bool(thread_ts) and thread_ts != row.get("ts") and row.get("subtype") != "thread_broadcast"


def relevant_rows(rows: Iterable[Mapping[str, Any]], mode: Mode, thread_ts: str | None = None) -> list[dict[str, Any]]:
    """The rows a watch looks at, oldest first: a channel's top-level rows and broadcasts, or a thread's replies."""
    picked: dict[str, dict[str, Any]] = {}
    for row in rows:
        ts = row.get("ts")
        if not isinstance(ts, str) or not ts:
            continue
        if mode == "channel" and is_reply(row):
            continue
        if mode == "thread" and ts == thread_ts:
            continue  # conversations.replies lists the parent first
        picked[ts] = dict(row)
    return sorted(picked.values(), key=lambda r: ts_key(r["ts"]))


def _base(channel: str, thread_ts: str | None) -> dict[str, Any]:
    data: dict[str, Any] = {"channel": channel}
    if thread_ts is not None:
        data["thread_ts"] = thread_ts
    return data


def _message_event(
    kind: str, row: Mapping[str, Any], channel: str, thread_ts: str | None, identity: Identity
) -> RawEvent:
    data = _base(channel, thread_ts)
    data["message"] = dict(row)
    data["own"] = identity.owns(row)
    data["broadcast"] = row.get("subtype") == "thread_broadcast"
    if kind == "message_edited":
        edited = row.get("edited") or {}
        data["edited"] = {"ts": edited.get("ts"), "user": edited.get("user")}
    return RawEvent(kind, data)


def _deleted_event(
    ts: str, known: Mapping[str, Any], channel: str, thread_ts: str | None, identity: Identity
) -> RawEvent:
    data = _base(channel, thread_ts)
    data.update(
        {
            "ts": ts,
            "user_id": known.get("author"),
            "bot_id": known.get("bot_id"),
            "text": known.get("text") or "",
            "own": identity.owns({"user": known.get("author"), "bot_id": known.get("bot_id")}),
        }
    )
    return RawEvent("message_deleted", data)


def diff_rows(
    previous: Snapshot | None,
    rows: Sequence[Mapping[str, Any]],
    *,
    mode: Mode,
    channel: str,
    identity: Identity,
    keep_from: Decimal | None,
    checked_from: Decimal | None,
    checked_until: Decimal | None,
    thread_ts: str | None = None,
    newest_unfetched: str | None = None,
    baseline_latest: str | None = None,
) -> DiffResult:
    """Diff one poll's rows (as :func:`relevant_rows` returns them) against the previous snapshot.

    - `keep_from`: the oldest ts the new snapshot keeps for edit checks (the recheck window); `None` keeps every row.
    - `checked_from`/`checked_until`: the span the rows cover completely. Only a known message inside it that is
      missing from the rows was deleted; `None` for `checked_from` means the rows start at the beginning.
    - `newest_unfetched`: the newest ts of the channel when the poll stopped paging before it reached it. The rows
      past the cap are then not read, and the catch-up becomes one `messages_skipped`.
    - `baseline_latest`: the cursor of a first poll when the rows hold nothing, such as an empty channel.

    Without a previous snapshot this is the baseline: no events, except that an archived channel ends at once.
    """
    known: dict[str, Any] = dict((previous or {}).get("messages") or {})
    latest = (previous or {}).get("latest")
    events: list[RawEvent] = []
    ended: str | None = None
    new_posts: list[RawEvent] = []
    kept: dict[str, Any] = {}
    seen_ts: set[str] = set()
    newest = latest

    for row in rows:
        ts = row["ts"]
        seen_ts.add(ts)
        subtype = row.get("subtype")
        if newest is None or ts_key(ts) > ts_key(newest):
            newest = ts
        if mode == "channel" and subtype in ARCHIVE_SUBTYPES:
            if previous is None or latest is None or ts_key(ts) > ts_key(latest):
                ended = "archived"
            continue
        if subtype in SYSTEM_SUBTYPES:
            if previous is not None and (latest is None or ts_key(ts) > ts_key(latest)):
                events.append(_channel_event(row, channel, thread_ts, identity))
            continue
        if keep_from is None or ts_key(ts) >= keep_from:
            kept[ts] = entry(row)
        if previous is None:
            continue
        if ts in known:
            before = known[ts]
            if subtype == TOMBSTONE and before.get("subtype") != TOMBSTONE:
                events.append(_deleted_event(ts, before, channel, thread_ts, identity))
            elif subtype != TOMBSTONE and (row.get("edited") or {}).get("ts") != before.get("edited"):
                events.append(_message_event("message_edited", row, channel, thread_ts, identity))
        elif latest is None or ts_key(ts) > ts_key(latest):
            if subtype != TOMBSTONE:
                new_posts.append(_message_event("message_posted", row, channel, thread_ts, identity))
        # A row at or below the cursor that the snapshot does not know aged in from outside the window: not new.

    if previous is not None:
        for ts, before in known.items():
            key = ts_key(ts)
            if ts in seen_ts or before.get("subtype") == TOMBSTONE:
                continue
            if checked_from is not None and key < checked_from:
                continue
            if checked_until is not None and key > checked_until:
                continue
            events.append(_deleted_event(ts, before, channel, thread_ts, identity))

    if newest_unfetched is not None and (newest is None or ts_key(newest_unfetched) > ts_key(newest)):
        newest = newest_unfetched
    if previous is not None:
        if len(new_posts) > CATCH_UP_CAP or (newest_unfetched is not None and new_posts):
            events.append(_skipped(new_posts, channel, thread_ts, newest, truncated=newest_unfetched is not None))
        else:
            events.extend(new_posts)

    if newest is None:
        newest = baseline_latest
    snapshot: Snapshot = {"latest": newest, "messages": dict(sorted(kept.items(), key=lambda kv: ts_key(kv[0])))}
    events.sort(key=_event_order)
    return DiffResult(snapshot, events, ended)


def _event_order(event: RawEvent) -> Decimal:
    ts = event.data.get("ts") or (event.data.get("message") or {}).get("ts") or event.data.get("oldest_ts") or "0"
    return ts_key(str(ts))


def _channel_event(row: Mapping[str, Any], channel: str, thread_ts: str | None, identity: Identity) -> RawEvent:
    data = _base(channel, thread_ts)
    data["subtype"] = row.get("subtype")
    data["message"] = dict(row)
    data["own"] = identity.owns(row)
    return RawEvent("channel_event", data)


def _skipped(
    posts: Sequence[RawEvent], channel: str, thread_ts: str | None, newest: str | None, truncated: bool
) -> RawEvent:
    data = _base(channel, thread_ts)
    data.update(
        {
            "count": len(posts),
            "truncated": truncated,
            "oldest_ts": posts[0].data["message"]["ts"] if posts else None,
            "newest_ts": newest if truncated else (posts[-1].data["message"]["ts"] if posts else None),
        }
    )
    return RawEvent("messages_skipped", data)


def gap_diff(
    previous: Snapshot, current: Snapshot, *, mode: Mode, channel: str, thread_ts: str | None
) -> list[RawEvent]:
    """The events between two snapshots, for a watcher that resubscribes to a resource others kept polling.

    The snapshots hold no raw rows, so a message posted in the gap carries what the snapshot keeps (`gap: true`), and
    messages posted in the gap that fell out of the recheck window become one `messages_skipped` without a count."""
    nobody = Identity()
    before: Mapping[str, Any] = previous.get("messages") or {}
    after: Mapping[str, Any] = current.get("messages") or {}
    old_latest = previous.get("latest")
    found: list[RawEvent] = []
    posted_from_window = 0
    for ts, now in after.items():
        if ts in before:
            if now.get("subtype") == TOMBSTONE and before[ts].get("subtype") != TOMBSTONE:
                found.append(_deleted_event(ts, before[ts], channel, thread_ts, nobody))
            elif now.get("edited") != before[ts].get("edited"):
                row = _row_of(ts, now, thread_ts)
                event = _message_event("message_edited", row, channel, thread_ts, nobody)
                event.data["gap"] = True
                found.append(event)
        elif old_latest is None or ts_key(ts) > ts_key(old_latest):
            row = _row_of(ts, now, thread_ts)
            event = _message_event("message_posted", row, channel, thread_ts, nobody)
            event.data["gap"] = True
            found.append(event)
            posted_from_window += 1
    oldest_kept = min((ts_key(ts) for ts in after), default=None)
    for ts, then in before.items():
        gone = ts not in after and then.get("subtype") != TOMBSTONE
        # A channel snapshot keeps only the window, so without a kept message nothing tells a deletion from aging out.
        in_window = oldest_kept is not None and ts_key(ts) >= oldest_kept
        if gone and (in_window or mode == "thread"):
            found.append(_deleted_event(ts, then, channel, thread_ts, nobody))
    new_latest = current.get("latest")
    if (
        mode == "channel"
        and new_latest is not None
        and (old_latest is None or ts_key(new_latest) > ts_key(old_latest))
        and posted_from_window == 0
    ):
        data = _base(channel, thread_ts)
        data.update({"count": None, "truncated": True, "oldest_ts": old_latest, "newest_ts": new_latest, "gap": True})
        found.append(RawEvent("messages_skipped", data))
    found.sort(key=_event_order)
    return found


def _row_of(ts: str, known: Mapping[str, Any], thread_ts: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {"type": "message", "ts": ts, "text": known.get("text") or ""}
    if known.get("author"):
        row["user"] = known["author"]
    if known.get("bot_id"):
        row["bot_id"] = known["bot_id"]
    if known.get("subtype"):
        row["subtype"] = known["subtype"]
    if known.get("edited"):
        row["edited"] = {"ts": known["edited"]}
    if thread_ts is not None:
        row["thread_ts"] = thread_ts
    return row
