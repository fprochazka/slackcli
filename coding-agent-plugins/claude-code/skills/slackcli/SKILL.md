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

```bash
slack messages send '#channel' "Hello world"
slack messages send '@john.doe' "Hello via DM"
slack messages send '#channel' --thread 1234567890.123456 "Reply in thread"
echo "Message" | slack messages send '#channel' --stdin
slack messages send '#channel' --file ./report.pdf           # Upload file
slack messages send '#channel' "Here's the report" --file ./report.pdf
slack messages send '#channel' "Message" --json              # Returns message timestamp
```

### Edit Messages

```bash
slack messages edit '#channel' 1234567890.123456 "Updated message"
slack messages edit '#channel' 1234567890.123456 "Updated" --json
```

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
slack resolve 'https://workspace.slack.com/archives/C0123456789/p1234567890123456'
slack resolve 'https://...' --json
```

Extracts workspace from URL automatically.

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

When composing messages, use Slack's mrkdwn syntax. **Slack does NOT support markdown tables** — use plain text alignment, bullet lists, or code blocks to present tabular data instead.


| Syntax | Result |
|--------|--------|
| `*bold*` | **bold** |
| `_italic_` | _italic_ |
| `` `code` `` | `code` |
| ` ```code block``` ` | code block |
| `<@U123456>` | @mention user |
| `<#C123456>` | #mention channel |
| `<!here>` | @here |
| `<!channel>` | @channel |
| `<https://url\|text>` | hyperlink |

Get user/channel IDs from `--json` output or `slack users get`.
