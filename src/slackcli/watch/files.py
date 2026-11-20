"""The watcher's side of `slack watch`: the resource dirs, and turning the daemon's raw rows into message objects.

The daemon sends Slack's raw rows and never resolves a user, so it spends its budget on history and replies only.
The watcher resolves names through slackcli's own users cache and conversations cache, as `messages list` does, so
an event's `message` is the same object `messages list --json` prints.

Layout: `<base>/<org>/channels/<C…>` and `<base>/<org>/threads/<C…>-<ts>`.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cli_watch_kit.atomic import atomic_write_json
from cli_watch_kit.client import WriterContext
from cli_watch_kit.events import Event
from cli_watch_kit.paths import sanitize_part
from cli_watch_kit.source import Target

if TYPE_CHECKING:
    from slackcli.client import SlackCli

README = """Files of one `slack watch`:

- events.jsonl: every reported event, one JSON object per line.
- baseline.json: the snapshot the watch resumes from.
- state.json: the newest snapshot: the newest ts seen, and the messages checked for edits and deletions.
"""

USER_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")

type NameResolver = Callable[[list[str]], dict[str, str]]


def resource_dir(org: str) -> Callable[[Path, Target], Path]:
    def place(base: Path, target: Target) -> Path:
        root = base / sanitize_part(org)
        if target.kind == "thread":
            return root / "threads" / sanitize_part(target.id.replace("/", "-"))
        return root / "channels" / sanitize_part(target.id)

    return place


def permalink(workspace_url: str | None, channel: str, ts: str, thread_ts: str | None = None) -> str | None:
    """The message's link, built the way Slack builds it, so no `chat.getPermalink` call is needed."""
    if not workspace_url or not ts:
        return None
    link = f"{workspace_url.rstrip('/')}/archives/{channel}/p{ts.replace('.', '')}"
    if thread_ts and thread_ts != ts:
        link += f"?thread_ts={thread_ts}&cid={channel}"
    return link


@dataclasses.dataclass
class MessageRenderer:
    """Turns raw rows into `messages list --json` objects, with user and channel names resolved."""

    users_of: NameResolver
    channels: Mapping[str, str]
    workspace_url: str | None

    def user_ids(self, row: Mapping[str, Any]) -> set[str]:
        ids = {str(row["user"])} if row.get("user") else set()
        ids.update(USER_MENTION.findall(str(row.get("text") or "")))
        for reaction in row.get("reactions") or []:
            ids.update(reaction.get("users") or [])
        edited = row.get("edited") or {}
        if edited.get("user"):
            ids.add(str(edited["user"]))
        return ids

    def message(self, row: Mapping[str, Any], users: dict[str, str]) -> dict[str, Any]:
        from slackcli.blocks import get_message_body_text
        from slackcli.models import Message, resolve_slack_mentions

        return Message.from_api(
            dict(row), users, dict(self.channels), get_message_body_text, resolve_slack_mentions
        ).to_dict(include_replies=False)

    def render(self, data: Mapping[str, Any]) -> dict[str, Any]:
        """The event data the agent gets, from the event data the daemon sent."""
        channel = str(data.get("channel") or "")
        out: dict[str, Any] = {"channel": channel, "channel_name": self.channels.get(channel) or channel}
        if data.get("thread_ts"):
            out["thread_ts"] = data["thread_ts"]
        row = data.get("message")
        rest = {k: v for k, v in data.items() if k not in ("channel", "message", "thread_ts")}
        if isinstance(row, Mapping):
            users = self.users_of(sorted(self.user_ids(row)))
            message = self.message(row, users)
            thread = data.get("thread_ts") or (row.get("thread_ts") if row.get("thread_ts") != row.get("ts") else None)
            out["message"] = message
            out["permalink"] = permalink(self.workspace_url, channel, str(row.get("ts") or ""), thread)
            edited = rest.get("edited")
            if isinstance(edited, Mapping) and edited.get("user"):
                rest["edited"] = {**edited, "user_name": users.get(str(edited["user"]), edited["user"])}
        elif data.get("ts"):
            from slackcli.models import resolve_slack_mentions

            ts = str(data["ts"])
            user = data.get("user_id")
            text = str(data.get("text") or "")
            ids = set(USER_MENTION.findall(text)) | ({str(user)} if user else set())
            users = self.users_of(sorted(ids))
            rest["user_name"] = users.get(str(user), user) if user else None
            rest["text"] = resolve_slack_mentions(text, users, dict(self.channels))
            out["permalink"] = permalink(self.workspace_url, channel, ts, data.get("thread_ts"))
        out.update(rest)
        return out


class SlackWriter:
    """Writes `state.json` and renders each event; drops the token user's own messages unless asked for them."""

    def __init__(self, context: WriterContext, renderer: MessageRenderer, include_own: bool) -> None:
        self.dir = context.resource_dir
        self.renderer = renderer
        self.include_own = include_own

    def snapshot(self, data: Any) -> None:
        if data is not None:
            atomic_write_json(self.dir / "state.json", data)

    def attach(self, events: list[Event]) -> list[Event]:
        out = []
        for event in events:
            if event.kind.startswith("watch_") or "channel" not in event.data:
                out.append(event)
                continue
            if event.data.get("own") and not self.include_own:
                continue
            out.append(dataclasses.replace(event, data=self.renderer.render(event.data)))
        return out


def renderer_for(slack: SlackCli, workspace_url: str | None) -> MessageRenderer:
    """A renderer that resolves users through slackcli's users cache and channels through its conversations cache."""
    return MessageRenderer(
        users_of=slack.get_user_display_names, channels=slack.get_channel_names(), workspace_url=workspace_url
    )
