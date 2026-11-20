"""`slack watch channel|thread|daemon`, on cli-watch-kit. Imported only when `slack watch` is used."""

from __future__ import annotations

import dataclasses
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import click
from cli_watch_kit.cli import WatchOptions, daemon_group, run_watch, watch_options
from cli_watch_kit.durations import format_duration, parse_duration
from cli_watch_kit.source import Target

from slackcli.watch.app import APP
from slackcli.watch.files import README, SlackWriter, renderer_for, resource_dir
from slackcli.watch.source import (
    DEFAULT_RECHECK_WINDOW,
    INTERNAL_PER_MINUTE,
    INTERVAL,
    UNLISTED_PER_MINUTE,
)

if TYPE_CHECKING:
    from cli_watch_kit.client import WriterContext

    from slackcli.client import SlackCli
    from slackcli.config import Config, OrgConfig

EXIT_USAGE = 2
TS = re.compile(r"^\d{9,}\.\d{1,6}$")
CHANNEL_ID = re.compile(r"^[CDG][A-Z0-9]+$")


@dataclass(frozen=True)
class ThreadRef:
    """One thread named on the command line: the channel as given (`#name`, an ID) and the root's ts."""

    channel: str
    ts: str
    workspace: str | None = None


def parse_thread_args(args: Sequence[str]) -> list[ThreadRef]:
    """Read `watch thread` arguments: a channel sets the channel for the timestamps after it, a URL names its own.

    `'#chan' 1700000000.000100 1700000500.000200 https://…/archives/C…/p…` is three threads, the first two in
    `#chan`. A URL of a reply names the reply's thread. Raises `click.UsageError` for anything else."""
    from slackcli.commands.resolve import parse_slack_url

    refs: list[ThreadRef] = []
    channel: str | None = None
    channel_used = True
    for arg in args:
        value = arg.strip()
        if value.startswith(("http://", "https://")):
            try:
                parsed = parse_slack_url(value)
            except ValueError as exc:
                raise click.UsageError(f"{arg!r}: {exc}") from exc
            if parsed.kind != "message" or not parsed.channel_id or not parsed.message_ts:
                raise click.UsageError(f"{arg!r} is not a link to a message or a thread")
            root = parsed.thread_ts if parsed.is_thread_reply and parsed.thread_ts else parsed.message_ts
            refs.append(ThreadRef(parsed.channel_id, root, parsed.workspace))
        elif TS.match(value):
            if channel is None:
                raise click.UsageError(f"{arg!r}: name the channel before the thread timestamp, as in '#general' {arg}")
            refs.append(ThreadRef(channel, value))
            channel_used = True
        elif value.startswith("#") or CHANNEL_ID.match(value):
            if not channel_used:
                raise click.UsageError(f"{channel!r} is followed by no thread timestamp")
            channel, channel_used = value, False
        else:
            raise click.UsageError(f"{arg!r} is not a channel, a thread timestamp or a Slack message link")
    if not channel_used:
        raise click.UsageError(f"{channel!r} is followed by no thread timestamp")
    if not refs:
        raise click.UsageError("name at least one thread: '#channel' <ts>, or a link to the thread")
    return refs


def parse_channel_arg(value: str) -> tuple[str, str | None]:
    """A channel argument as `#name`, an ID, or a channel link; returns the reference and the link's workspace."""
    if value.startswith(("http://", "https://")):
        from slackcli.commands.resolve import parse_slack_url

        try:
            parsed = parse_slack_url(value)
        except ValueError as exc:
            raise click.UsageError(f"{value!r}: {exc}") from exc
        if parsed.kind != "conversation" or not parsed.channel_id:
            raise click.UsageError(f"{value!r} is not a link to a channel; `slack watch thread` takes message links")
        return parsed.channel_id, parsed.workspace
    if value.startswith("#") or CHANNEL_ID.match(value):
        return value, None
    raise click.UsageError(f"{value!r} is not a channel: give '#name', a channel ID or a channel link")


def parse_recheck_window(value: str) -> float:
    try:
        seconds = parse_duration(value)
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--recheck-window") from exc
    if seconds < 0:
        raise click.BadParameter("the window cannot be negative", param_hint="--recheck-window")
    return seconds


@dataclass
class Session:
    """The org, token and Slack client of a watch, resolved before it starts."""

    config: Config
    org: OrgConfig
    slack: SlackCli
    workspace_url: str | None
    team: str | None


def select_org(workspaces: Sequence[str | None]) -> Session:
    """The org of `--org`/`SLACK_ORG`, else of the links' workspace, else the default; checks the token once."""
    from slack_sdk.errors import SlackApiError

    from slackcli.context import get_context
    from slackcli.errors import get_error_code

    ctx = get_context()
    try:
        config = ctx.get_config()
    except (FileNotFoundError, ValueError) as exc:
        click.echo(f"{APP.status_prefix}error: {exc}", err=True)
        sys.exit(1)
    if ctx.org_name is None:
        named = sorted({w for w in workspaces if w})
        orgs = set()
        for workspace in named:
            try:
                orgs.add(config.get_org(workspace).name)
            except ValueError:
                continue
        if len(orgs) > 1:
            raise click.UsageError(f"the links belong to several orgs ({', '.join(sorted(orgs))}); watch them apart")
        if orgs:
            ctx.org_name = next(iter(orgs))
    try:
        org = ctx.get_org()
    except ValueError as exc:
        click.echo(f"{APP.status_prefix}error: {exc}", err=True)
        sys.exit(EXIT_USAGE)
    slack = ctx.get_slack_client()
    try:
        auth = slack.client.auth_test()
    except SlackApiError as exc:
        click.echo(
            f"{APP.status_prefix}error: Slack refused the token of org {org.name}: {get_error_code(exc)}", err=True
        )
        sys.exit(EXIT_USAGE)
    return Session(config, org, slack, auth.get("url"), auth.get("team"))


def resolve_channel_ref(slack: SlackCli, ref: str) -> tuple[str, str]:
    from slackcli.commands.messages import resolve_channel

    return resolve_channel(slack, ref)


def poll_line(kind: str, unlisted: bool) -> str:
    method = "conversations.history" if kind == "channel" else "conversations.replies"
    per_minute = UNLISTED_PER_MINUTE if unlisted else INTERNAL_PER_MINUTE
    fits = int(per_minute * INTERVAL / 60)
    what = "channel" if kind == "channel" else "thread"
    stretch = (
        f"more than {fits} {what} watches of this org poll less often"
        if fits >= 1
        else f"each {what} watch of this org adds {60 / per_minute:.0f}s to the interval"
    )
    return (
        f"poll: every {INTERVAL:.0f}s per {what}, one {method} call each; at most {per_minute:g}/min for this org, "
        f"shared by every {what} watch ({stretch})"
    )


def _run(
    kind: str,
    session: Session,
    targets: list[Target],
    names: list[str],
    options: WatchOptions,
    include_own: bool,
    recheck_window: float,
    restart: str,
) -> None:
    rate_limit = session.config.rate_limit_for(session.org)
    unlisted = rate_limit == "unlisted"
    if kind == "channel" and not options.events:
        # Joins, topic changes and pins are noise for most watches; `--events=messages,system` brings them back.
        options = dataclasses.replace(options, exclude_events=(*options.exclude_events, "system"))
    renderer = renderer_for(session.slack, session.workspace_url)

    def writer(context: WriterContext) -> SlackWriter:
        return SlackWriter(context, renderer, include_own)

    header = [f"org: {session.org.name}" + (f" ({session.workspace_url})" if session.workspace_url else "")]
    header += names
    header.append(poll_line(kind, unlisted))
    if unlisted:
        header.append('rate limit: unlisted app (rate_limit = "unlisted" in the config): 1 call/min per method')
    header.append(
        "own messages: reported (--include-own)"
        if include_own
        else "own messages: not reported (--include-own reports them)"
    )
    if kind == "channel":
        header.append(
            f"recheck window: {format_duration(recheck_window)} (edits and deletions of older messages are not seen)"
            if recheck_window > 0
            else "recheck window: 0 (only new messages are reported, no edits or deletions)"
        )
    source_options: dict[str, Any] = {"rate_limit": rate_limit}
    if kind == "channel":
        source_options["recheck_window"] = recheck_window
    code = run_watch(
        APP,
        targets,
        options,
        params={"token": session.org.token},
        source_options=source_options,
        writer=writer,
        resource_dir=resource_dir(session.org.name),
        readme=README,
        restart=restart,
        header=header,
    )
    sys.exit(code)


def _restart(command: str, args: Sequence[str], org: str, include_own: bool, recheck: float | None) -> str:
    parts = [command, *(f"'{a}'" if a.startswith("#") else a for a in args)]
    if include_own:
        parts.append("--include-own")
    if recheck is not None and recheck != DEFAULT_RECHECK_WINDOW:
        parts.append(f"--recheck-window={format_duration(recheck) if recheck else '0'}")
    parts.append(f"--org={org}")
    return " ".join(parts)


INCLUDE_OWN = click.option(
    "--include-own",
    is_flag=True,
    help="Also report the messages the token's own user (or bot) posted, edited, deleted.",
)


@click.group("watch")
def watch() -> None:
    """Follow Slack channels and threads and report new, edited and deleted messages as JSON events, one per line
    on stdout, or as messages into the Claude Code session that started the watch. Read-only.

    All watches on this machine share one daemon, which polls each channel or thread once for all its watchers.
    """


@watch.command("channel")
@click.argument("channels", nargs=-1, required=True)
@INCLUDE_OWN
@click.option(
    "--recheck-window",
    default="10m",
    show_default=True,
    help="How far back edits and deletions are noticed, such as 10m or 1h; 0 reports only new messages.",
)
@watch_options
def channel_command(channels: tuple[str, ...], include_own: bool, recheck_window: str, options: WatchOptions) -> None:
    """Watch the top-level messages of channels ('#name', ID or channel link), thread broadcasts included and other
    thread replies never.

    Runs until stopped, or until a channel is archived or out of reach. Joins, topic changes and pins
    (channel_event) are left out unless --events names them.
    """
    window = parse_recheck_window(recheck_window)
    parsed = [parse_channel_arg(c) for c in channels]
    session = select_org([w for _, w in parsed])
    targets: list[Target] = []
    names: list[str] = []
    for ref, _ in parsed:
        channel_id, name = resolve_channel_ref(session.slack, ref)
        target = Target("channel", channel_id)
        if target in targets:
            continue
        targets.append(target)
        names.append(f"channel: #{name} ({channel_id})" if name != channel_id else f"channel: {channel_id}")
    restart = _restart("slack watch channel", [t.id for t in targets], session.org.name, include_own, window)
    _run("channel", session, targets, names, options, include_own, window, restart)


@watch.command("thread")
@click.argument("threads", nargs=-1, required=True)
@INCLUDE_OWN
@watch_options
def thread_command(threads: tuple[str, ...], include_own: bool, options: WatchOptions) -> None:
    """Watch the replies of threads: '#channel' <ts> [<ts> ...], or links to the thread or to one of its replies.

    A channel applies to the timestamps after it, a link names its own channel. Reports new, edited and deleted
    replies, never the channel's other messages. Runs until stopped, or until the thread is gone.
    """
    refs = parse_thread_args(threads)
    session = select_org([r.workspace for r in refs])
    targets: list[Target] = []
    names: list[str] = []
    for ref in refs:
        channel_id, name = resolve_channel_ref(session.slack, ref.channel)
        target = Target("thread", f"{channel_id}/{ref.ts}")
        if target in targets:
            continue
        targets.append(target)
        where = f"#{name}" if name != channel_id else channel_id
        names.append(f"thread: {channel_id}/{ref.ts} in {where}")
    args: list[str] = []
    for target in targets:
        channel_id, ts = target.id.split("/", 1)
        args += [channel_id, ts]
    restart = _restart("slack watch thread", args, session.org.name, include_own, None)
    _run("thread", session, targets, names, options, include_own, DEFAULT_RECHECK_WINDOW, restart)


watch.add_command(daemon_group(APP))
