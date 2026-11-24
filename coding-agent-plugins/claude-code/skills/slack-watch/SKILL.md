---
name: slack-watch
description: >-
  This skill should be used when the session has to wait for something on Slack or react to it as it happens:
  "watch this channel", "tell me when someone replies", "let me know when they answer in the thread", "watch #deploys",
  "wait for a reply to my message", "follow this thread", or when a `<slack-watcher>` message arrives. Covers starting
  `slack watch` in the background, its startup header, event filters, reacting to its messages, stopping it, and the
  files it writes.
trigger-keywords: slack watch, slack-watcher, watch channel, watch thread, wait for reply, slack reply
allowed-tools: Bash(slack watch channel:*), Bash(slack watch thread:*), Bash(slack watch daemon status:*), Bash(slack watch daemon logs:*), Bash(slack watch --help), Bash(slack watch daemon --help), Bash(slack watch daemon stop --help)
---

# slack watch

`slack watch` follows Slack channels and threads and reports each new, edited or deleted message as an event. Under Claude Code the events arrive in this conversation as messages, so the session learns about a reply without asking. It only reads Slack; it never posts anything. It comes with the `slack` command of slackcli (the `slackcli` skill covers the rest of it, including `slack config` and `--org`).

## Watch or read once

- "What did they say?": read once with `slack messages list '#channel'` or `slack messages list '#channel' <thread ts>`.
- "Tell me when they answer": start a watcher. **Never poll** with `sleep`, a loop or repeated `messages list` calls. All watchers on the machine share one daemon that reads each channel or thread once for all of them, within Slack's rate limits.

## Starting a watcher

Start every watcher with the Bash tool and `run_in_background: true`:

```bash
slack watch channel '#deploys'                                   # top-level messages of a channel
slack watch channel '#deploys' '#incidents' C0123456789          # several channels in one watcher
slack watch thread '#general' 1700000000.123456                  # replies of one thread
slack watch thread '#general' 1700000000.123456 1700000500.000100 '#random' 1700000900.000200   # a channel applies to the timestamps after it
slack watch thread 'https://myworkspace.slack.com/archives/C0123456789/p1700000000123456'          # a link to the thread or to one of its replies
```

- A channel watch reports top-level messages only, and a reply also sent to the channel (`broadcast: true`). It never reports other thread replies: watch the thread for those. A thread watch reports only that thread's replies.
- Waiting for an answer to a message you just sent: `slack messages send` prints the message's ts; watch it as a thread (`slack watch thread '#channel' <ts> --until=message_posted`).
- `--until=<kind>` ends after the first event of that kind, `--timeout=<duration>` after that long, `--idle-after=<duration>` when nothing was reported for that long.
- Your own messages are not reported, so a message you send does not wake you. `--include-own` reports them, with `own: true`.
- Put `--org=<org>` after the command when the session needs a workspace other than the default, as with every `slack` command.
- **Start watchers from the main session.** Claude Code has no route for messages to a subagent, so a subagent's watcher would message the main session anyway. Forward the events a subagent needs with SendMessage: the event lines, not a summary.

### What was there before the watch started

The start is silent: messages already in the channel or thread are not reported. When the user also wants those, catch up in this order, so nothing falls between the two:

1. Start the watch in the background.
2. Wait for its `watch_started` event: the baseline is taken.
3. Read once with `slack messages list '#channel' --tail 20` or `slack messages list '#channel' <thread ts>`.

A message in both the list and a later event is fine.

### Leave `--events-stream` at its default

Start every watcher without `--events-stream`. The default, `auto`, sends each event to this session as a message when the session accepts them, and otherwise falls back to stdout and says why. With `--events-stream=stdout` nothing tells you when an event arrives; that mode is for scripts and terminals.

## The startup header

Read the header in the background task's output first. Each line starts with `slack watch: `:

- `resource:`, `dir:` and `changelog:` per watched channel or thread; take paths from here and from the events, never build them.
- `stream:` confirms the mode. A `stream fallback:` line means the events do not arrive as messages: tell the user the reason; the events are in the task output and in `events.jsonl`.
- `org:` is the workspace, `channel:` or `thread:` names each target with its channel name.
- `poll:` says how often each target is read and how many fit in the rate limit. `own messages:` and `recheck window:` show those two settings.
- `events:` and `ignored:` show the filter, `ends:` the end options, `daemon:` the shared daemon or `--no-daemon`.
- `restart with:` is the command to start again with another filter.

## Filters

`--events=<kinds or groups>` reports only those, `--exclude-events=<kinds or groups>` leaves them out. Groups: `messages` (posted, edited, deleted, skipped) and `system` (`channel_event`: joins, leaves, topic, purpose and name changes, pins); `lifecycle` (`watch_started`, `watch_degraded`, `watch_ended`) is always on. A channel watch leaves `system` out unless `--events` names it: `--events=messages,system`.

- Only new messages: `--events=message_posted`.
- The first reply and stop: `slack watch thread '#channel' <ts> --until=message_posted`.

## The messages

Each batch arrives as one message, one JSON event per line:

```text
<slack-watcher resource="thread C0123456789/1700000000.123456" seq="2-3">
{"kind":"message_posted","seq":2,"data":{"channel":"C0123456789","channel_name":"deploys","thread_ts":"1700000000.123456","message":{"ts":"1700000100.000200","user_id":"U0123456789","user_name":"jane.doe","text":"Looks good, ship it","…":"…"},"permalink":"https://…/archives/C0123456789/p1700000100000200?thread_ts=1700000000.123456&cid=C0123456789","own":false,"broadcast":false}}
{"kind":"message_edited","seq":3,"data":{"channel":"C0123456789","message":{"…":"…"},"edited":{"ts":"1700000160.000000","user":"U0123456789","user_name":"jane.doe"},"…":"…"}}
</slack-watcher>
```

- `message` is the same object `slack messages list --json` prints: `user_name`, `text` with mentions resolved, `files`, `attachments`, `bot_id`/`bot_name`/`username` for bots and webhooks, `subtype`, `edited`.
- A batch above 64 KB arrives as counts per kind, and a gap in `seq` means messages were lost: read `events.jsonl` from the last `seq` you saw.

## The events

| Kind | Watch | Data besides `channel`, `channel_name` (and `thread_ts` for a thread) |
|------|-------|------------------------------------------------------------------------|
| `message_posted` | channel, thread | `message`, `permalink`, `own`, `broadcast` (a thread reply also sent to the channel) |
| `message_edited` | channel, thread | `message` as it is now, `edited` (`ts`, `user`, `user_name`); Slack keeps no earlier text |
| `message_deleted` | channel, thread | `ts`, `user_id`, `user_name`, `text` (the last text the watch saw), `permalink` |
| `messages_skipped` | channel, thread | `count`, `oldest_ts`, `newest_ts`, `truncated`: too many missed messages to report one by one |
| `channel_event` | channel, with `--events=system` | `subtype` (`channel_join`, `channel_topic`, `pinned_item`, …), `message` |

A channel watch sees edits and deletions only inside its recheck window (default 10 minutes, `--recheck-window=1h` for longer, `0` for none). A thread watch sees every edit and deletion in the thread.

## Reacting

- **`message_posted`**: read `message.text` and who wrote it (`user_name`, or `bot_name`/`username` for a bot); for files, `slack files download <id>`. Act as the caller's purpose says; do not reply on Slack unless the user asked you to.
- **`message_edited`**: the answer changed; reread `message.text`.
- **`message_deleted`**: the message is gone; mention it only if it mattered to the task.
- **`messages_skipped`**: read the missed messages once: `slack messages list '#channel' --after <oldest_ts> --head <count>` (or the thread with its ts).
- **`watch_degraded`**: `auth`, tell the user the token was refused; `rate_limited`, nothing to do, the daemon waits for Slack's Retry-After; `error`, nothing to do unless it repeats.
- **`watch_ended`**: the reason says why: `archived`, `not_found` (the channel or thread is gone), `no_access` (the token cannot read the channel, such as a bot that is not invited), `until`, `timeout`, `idle`, `session_gone`, or a signal.

## Stopping

Stop a watcher by stopping its background task. To change a filter, stop it and start the `restart with:` command. Never leave two watchers on the same channel or thread with the same filter.

## The files

Each channel or thread has one dir under the session scratchpad, `slack-watch/<org>/channels/<C…>` or `slack-watch/<org>/threads/<C…>-<ts>`, with a `README.txt`, `events.jsonl` (every event), `state.json` (the newest snapshot) and `baseline.json` (what the watcher resumes from).

`slack watch daemon status` shows the daemon's subscriptions, its calls per Slack method and the rate budget; `slack watch daemon logs` its log; `slack watch daemon stop` stops it (running watchers start a new one); it asks for permission, because it interrupts every watch on the machine.

## Limits

- A message is seen within the poll interval: 20 s for up to 8 channel watches (and separately up to 8 thread watches) of one workspace, longer with more.
- Edits and deletions of channel messages older than the recheck window are not seen; Slack's history never reports them.
- Reactions are not reported.
- A session in bypass-permissions mode holds the messages of a watcher it cannot verify as its own child until the user lets them in; the changelog still has every event. Start `slack watch` from this session's own Bash tool so Claude Code can verify it. If the messages are still held, tell the user rather than suggesting `"crossSessionInbound": "accept"` in their settings: that lets every local sender, other sessions included, drive a session that runs commands without asking. One run with `claude --settings '{"crossSessionInbound":"accept"}'`, or auto mode, is the narrower choice.
