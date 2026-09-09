---
name: slackcli
description: CLI for interacting with Slack workspaces. Use when working with Slack to read messages, list channels, send messages, search, add reactions, or resolve Slack URLs. Triggered by requests involving Slack data, channel exploration, message searches, or Slack automation.
trigger-keywords: slack, slack message, slack channel, slack dm, slack thread, slack reaction, slack search
allowed-tools: Bash(slack --help), Bash(slack config:*), Bash(slack conversations list:*), Bash(slack messages list:*), Bash(slack search messages:*), Bash(slack search files:*), Bash(slack users list:*), Bash(slack users search:*), Bash(slack users get:*), Bash(slack files download:*), Bash(slack pins list:*), Bash(slack scheduled list:*), Bash(slack resolve:*)
---

# slackcli

Command-line interface for Slack API operations.

## First Step: Check Configuration

**Run `slack config` at the start of every session** to check workspace setup:

```bash
slack config
```

Check the last line of output:
- `Using org from SLACK_ORG: <name>` → No `--org` needed, commands use this workspace
- `No org selected (use --org or SLACK_ORG)` → Must pass `--org=<workspace>` to every command

The output also shows available workspaces in the "orgs" section.

## Flag Placement

**Always place flags after the full command path**, not between `slack` and the command group. This ensures command prefix matching works correctly for permissions.

```bash
# Correct:
slack conversations list --org=mycompany
slack messages list '#channel' --org=work --json

# Wrong:
slack --org=mycompany conversations list
```

## Workspace Selection

```bash
# When SLACK_ORG env is set (no --org needed):
slack conversations list

# When no org is selected (--org required):
slack conversations list --org=mycompany
```

If the user hasn't specified a workspace and no default is configured, **ask them which workspace to use** (show the available orgs from `slack config`).

## Global Flags

| Flag | Description |
|------|-------------|
| `--org` | Workspace name (required if SLACK_ORG not set) |
| `--verbose` | Enable debug logging |
| `--json` | JSON output (available on most commands) |

## Conversations

```bash
slack conversations list              # All conversations (cached)
slack conversations list --public     # Public channels only
slack conversations list --private    # Private channels only
slack conversations list --dms        # DMs and group DMs only
slack conversations list --member     # Channels you're a member of
slack conversations list --non-member # Channels you're not in
slack conversations list --refresh    # Force cache refresh
```

## Messages

### List Messages

Direction flags control which slice of history to fetch. When no direction flag
is given, the default is `--tail 25`. Results are always displayed
oldest → newest.

| Flag | Meaning |
|------|---------|
| `--tail N` | Last N messages in the window (default direction; default N=25) |
| `--head N` | First N messages in the window, oldest first |
| `--after TS` | Messages after cursor timestamp TS (default 25, override with `--head N`) |
| `--before TS` | Messages before cursor timestamp TS (default 25, override with `--tail N`) |

Allowed combinations: `--head N` alone, `--tail N` alone, `--after TS` alone,
`--before TS` alone, `--after TS --head N`, `--before TS --tail N`.
`--head + --tail`, `--after + --before`, `--head + --before`, and
`--tail + --after` are rejected.

Direction flags compose with time-window flags (`--since`, `--until`, `--today`,
`--last-7d`, `--last-30d`), which are orthogonal bounds on the window.

```bash
slack messages list '#channel'                                      # Last 25 messages
slack messages list '#channel' --tail 5                             # Last 5 messages
slack messages list '#channel' --head 5                             # First 5 in window
slack messages list '#channel' --after 1234567890.123456            # After cursor (default 25)
slack messages list '#channel' --before 1234567890.123456           # Before cursor (default 25)
slack messages list '#channel' --after 1234567890.123456 --head 5   # Next 5 newer
slack messages list '#channel' --before 1234567890.123456 --tail 5  # Previous 5 older

slack messages list '#channel' --today                    # Today only
slack messages list '#channel' --last-7d                  # Last 7 days
slack messages list '#channel' --last-30d                 # Last 30 days
slack messages list '#channel' --since 2024-01-15         # Since specific date
slack messages list '#channel' --since 7d --until 3d      # Relative range
slack messages list '#channel' --head 100 --since 2024-01-01 --until 2024-02-01

slack messages list '#channel' --with-threads             # Include thread replies
slack messages list '#channel' --reactions=counts         # Show reaction counts
slack messages list '#channel' --reactions=names          # Show who reacted
slack messages list C0123456789 --json                    # Channel ID, JSON output
```

### Paginating Further

Every listing reports whether more messages exist on either side of the returned
slice.

**Text mode** — a trailing footer is printed when there is more to fetch, e.g.:

```
[older: --before 1234.5678 | newer: --after 2345.6789]
```

In thread view the labels change to `earlier replies` / `later replies` so
it's clear the cursors page within the thread.

Feed either cursor back into a new call to page further:

```bash
slack messages list '#channel' --before 1234567890.123456
slack messages list '#channel' --after 2345678901.234567
```

**JSON mode** — the envelope includes `has_more_before`, `has_more_after`,
`next_before_ts`, and `next_after_ts`. When viewing a thread in `--tail` mode,
it may also include `thread_parent_omitted: true` to signal that the root
message was replaced with a placeholder.

### Threads

```bash
slack messages list '#channel' 1234567890.123456           # View a thread
slack messages list '#channel' 1234567890.123456 --tail 10 # Last 10 replies
slack messages list '#channel' 1234567890.123456 --head 10 # Root + first 9 replies
```

The thread root counts toward the requested count. With `--tail N`, if the
thread has more than N messages, the parent is rendered as a placeholder line
and the last N replies are shown — use `--head` to see the real parent.

### Send Messages

Send to channels or DMs. Target can be `#channel`, `@username`, `@email@example.com`, or IDs.

Write the body in ordinary Markdown — the CLI converts it to Slack's formatting (see [Message Formatting](#message-formatting)). For a multi-line body, write it to a file and pipe it in with `--stdin`; a `\n` inside a shell argument is sent as two literal characters, not a newline.

```bash
slack messages send '#channel' "Hello world"
slack messages send '@john.doe' "Hello via DM"
slack messages send '#channel' --thread 1234567890.123456 "Reply in thread"
slack messages send '#channel' --file ./report.pdf           # Upload file
slack messages send '#channel' "Here's the report" --file ./report.pdf
slack messages send '#channel' "Message" --json              # Returns message timestamp

# Multi-line body: write the Markdown to a file, then pipe it in
cat /tmp/scratch/deploy-report.md | slack messages send '#channel' --stdin
```

Hand-built Block Kit payloads (`--blocks`) are an escape hatch for layouts Markdown cannot express; see `references/block-kit.md`.

### Edit Messages

The new text is Markdown too, converted the same way as `send`.

```bash
slack messages edit '#channel' 1234567890.123456 "Updated message"
slack messages edit '#channel' 1234567890.123456 "Updated" --json

# Drop the link previews, keeping the message as it is
slack messages edit '#channel' 1234567890.123456 --remove-link-previews

# Same, for a message that is a thread reply
slack messages edit '#channel' 1234567890.123456 --remove-link-previews --thread 1234567890.000001

# Change the text and drop the previews in one edit
slack messages edit '#channel' 1234567890.123456 "Updated message" --remove-link-previews
```

`--remove-link-previews` deletes the previews Slack and other apps unfurled into the message. Without new text the message keeps its content and only loses the previews. The removal sticks — a link left in the text is not unfurled again — though a later edit that changes the links may produce a fresh preview. A preview an app posted (Linear, GitHub, ...) cannot be brought back except by deleting the message and posting it again. `--thread <parent ts>` is needed to find a thread reply when no new text is given.

### Delete Messages

```bash
slack messages delete '#channel' 1234567890.123456         # With confirmation
slack messages delete '#channel' 1234567890.123456 --force # Skip confirmation
```

## Search

### Search Messages

```bash
slack search messages "quarterly report"
slack search messages "bug fix" --in '#engineering'
slack search messages "deadline" --from '@john.doe'
slack search messages "meeting" --after 7d
slack search messages "project" --before 2024-01-15 --after 2024-01-01
slack search messages "query" --sort timestamp --sort-dir desc
slack search messages "query" --limit 50 --page 2
```

### Search Files

```bash
slack search files "report.pdf"
slack search files "spreadsheet" --in '#finance'
slack search files "presentation" --from '@jane.doe'
slack search files "budget" --after 30d
```

## Users

```bash
slack users list                    # List all users (cached)
slack users list --refresh          # Force refresh
slack users list --bots --deleted   # Include bots and deleted users
slack users search "john"           # Search by name/email
slack users get @john.doe           # Get user details
slack users get john@example.com    # Get by email
slack users get U0123456789         # Get by user ID
```

## Files

Files are downloaded to `/tmp/slackcli-<random>/`.

```bash
slack files download F0ABC123DEF                      # Download by file ID
slack files download 'https://files.slack.com/...'   # Download by URL
slack files download F0ABC123DEF --json               # Output download details as JSON
```

## Reactions

```bash
slack reactions add '#channel' 1234567890.123456 thumbsup    # Add reaction
slack reactions add '#channel' 1234567890.123456 :+1:        # Colons stripped
slack reactions remove '#channel' 1234567890.123456 thumbsup # Remove reaction
```

## Pins

```bash
slack pins list '#channel'                           # List pinned messages
slack pins add '#channel' 1234567890.123456          # Pin a message
slack pins remove '#channel' 1234567890.123456       # Unpin a message
```

## Scheduled Messages

```bash
slack scheduled list                                 # List all scheduled
slack scheduled list '#channel'                      # Filter by channel
slack scheduled create '#channel' "in 1h" "Reminder!"
slack scheduled create '#channel' "in 30m" "Meeting soon"
slack scheduled create '#channel' "tomorrow" "Daily standup"
slack scheduled create '#channel' "tomorrow 9am" "Good morning!"
slack scheduled create '#channel' "2025-02-03 09:00" "Team meeting"
slack scheduled create '#channel' --thread 1234567890.123456 "in 1h" "Reply"
slack scheduled delete S0123456789                   # Delete by scheduled ID
```

## Resolve Slack URLs

```bash
slack resolve 'https://workspace.slack.com/archives/C0123456789/p1234567890123456'          # Message
slack resolve 'https://workspace.slack.com/archives/C0123456789/p1234567890123456?thread_ts=1234567890.123456'  # Thread reply
slack resolve 'https://workspace.slack.com/archives/C0123456789'                            # Channel metadata
slack resolve 'https://workspace.slack.com/messages/C0123456789'                            # Channel (legacy URL)
slack resolve 'https://workspace.slack.com/team/U0123456789'                                # User
slack resolve 'https://workspace.slack.com/files/U0123456789/F0123456789/report.pdf'        # File
slack resolve 'https://app.slack.com/client/T0123456789/C0123456789'                        # Channel (web client)
slack resolve 'https://app.slack.com/client/T0123456789/C0123456789/thread/C0123456789-1234567890.123456'  # Thread root
slack resolve 'https://...' --json
```

Extracts workspace from URL automatically. An `app.slack.com` URL names no workspace, so it needs `--org` or `SLACK_ORG`.

A channel URL resolves even when you are not a member. JSON output carries a `type` field: `message`, `conversation`, `user` or `file`.

## References

### Channels
- `#channel-name` - Channel name with hash
- `C0123456789` - Channel ID

### Users
- `@username` - Username with @
- `@email@example.com` - Email with @
- `U0123456789` - User ID

### Message Timestamps

Format: `1234567890.123456`. Get them from:
- `--json` output of any message command
- Thread reply indicator in text output
- Slack URL (the `p` parameter, add decimal before last 6 digits)
- The has-more footer / `next_before_ts` / `next_after_ts` fields when paginating

## Message Formatting

Write Markdown. The CLI detects it and converts the body to Slack rich text, so a message renders with real lists, syntax-highlighted code and labelled links instead of raw markup.

| Write | Slack shows |
|---|---|
| `**bold**`, `*italic*`, `~~strike~~`, `` `code` `` | bold, italic, strikethrough, inline code |
| `# Heading` | a bold line (Slack has no headings) |
| `- item` / `1. item`, nested by indentation | real bullet / numbered lists, nesting shown by indent |
| ```` ```sql ```` … ```` ``` ```` | code block with syntax highlighting and line numbers |
| `> quote` | quote bar |
| `[text](https://url)` | link showing *text* |
| `\| a \| b \|` table | monospace block (Slack has no table element) |
| blank line | paragraph break |

Slack's own tokens work inside Markdown and are the only way to write them:

| Syntax | Result |
|--------|--------|
| `<@U123456>` | @mention user |
| `<#C123456>` | #mention channel |
| `<!here>`, `<!channel>` | @here, @channel |
| `<!subteam^S123456>` | @mention a user group |
| `:emoji_name:` | emoji |
| `<https://url\|text>` | hyperlink (a Markdown link works too) |

Get user/channel IDs from `--json` output or `slack users get`. Other `<...>` forms, such as `<#G123456>` or `<!date^1234567890^{date}>`, are sent as literal text. Anything inside a code fence or backticks is sent verbatim.

**Detection.** Conversion happens when the body carries a shape that exists only in Markdown: `**bold**`, `[text](url)`, a heading, a `-` or `1.` list line, a ```` ```lang ```` fence, a table row, `~~strike~~`. A body without one of those goes out unchanged as Slack mrkdwn (`*bold*`, `_italic_`), which keeps existing callers working. The dialects differ — in Markdown `*x*` is italic and `**x**` is bold — so pick one per message. `--format=markdown` forces the conversion for a body with no obvious signal; `--format=mrkdwn` sends the body as it stands. `send`, `edit` and `scheduled create` report the path taken in `--json` as `"format": "markdown" | "mrkdwn" | "blocks"`.

**Multi-line bodies**: write the Markdown to a file and pipe it in (`cat message.md | slack messages send '#channel' --stdin`); a shell argument cannot carry a newline written as `\n`.

**Length.** A message is at most 4000 characters and a longer one is refused before it reaches Slack: split it, for example into a thread. Rich text holds more, so a long Markdown body still posts — only the notification preview is shortened.

**Signature.** Messages sent from a coding agent get a small grey footer ("— sent from Claude Code") automatically, so never write a "sent by Claude" line of your own. The user turns it off with `agent_signature = "off"` in their config. `SLACK_AGENT_SIGNATURE=off|plain|marketing` overrides that key for one command.

## Additional Resources

### Reference Files

- **`references/block-kit.md`** — posting hand-built Block Kit JSON with `--blocks`: accepted shapes, fallback text, limits, an example payload.
