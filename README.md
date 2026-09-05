# slackcli

A command-line interface for Slack, designed for both humans and AI agents.

## Not the Official Slack CLI

This project is **not** affiliated with Slack or Salesforce. If you're looking to build Slack apps with workflows, triggers, and datastores, check out the [official Slack CLI](https://api.slack.com/automation/cli).

**What's the difference?**

| Feature | slackcli (this project) | Official Slack CLI |
|---------|-------------------------|-------------------|
| **Purpose** | Direct API access for automation & scripting | Build and deploy Slack apps |
| **Use cases** | Read messages, search channels, AI agent integration | Workflows, triggers, datastores, app development |
| **Authentication** | Bot/User OAuth tokens | Slack app credentials |
| **Complexity** | Simple Python CLI | Full development framework |

**When to use slackcli:**
- Reading and searching Slack messages
- Sending, editing, and deleting messages
- Sending direct messages to users
- Uploading files to channels
- Adding and removing reactions
- Pinning and unpinning messages
- Scheduling messages for later delivery
- Managing and searching users
- Automating channel exploration
- Integrating Slack into AI agents and scripts
- Quick API interactions from the terminal

## Installation

First clone the repository, then:

```bash
# Install globally with uv (editable mode)
uv tool install -e .
```

Editable mode means updates are automatic after `git pull` - no reinstall needed.

## Configuration

Create a configuration file at `~/.config/slackcli/config.toml`:

```toml
# Default organization (optional)
default_org = "myworkspace"

# How messages from an AI agent are signed (optional, default "marketing")
agent_signature = "marketing"  # off | plain | marketing

[orgs.myworkspace]
token = "xoxp-your-user-token-here"

[orgs.another-workspace]
token = "xoxp-another-token"
```

### Agent signature

Messages sent by an AI coding agent carry a small grey footer naming it, so a reader can tell them from what a person wrote:

```
— sent from Claude Code
```

```toml
agent_signature = "marketing"  # off | plain | marketing
```

`marketing` (the default) links the agent's name to this project, `plain` prints the name alone, and `off` never signs anything. The footer is a `context` block, which Slack never unfurls, and it is added only when the CLI is running under an agent: a person typing the same command signs nothing.

Detection reads the environment variables the known harnesses set — Claude Code, Gemini CLI, Codex, Cursor, Cline, OpenCode, Goose, Antigravity, Augment, Junie, Kimi Code, Grok Build, OpenClaw, Trae, Pi — plus the generic `AGENT=<slug>` and `AI_AGENT=<slug>`, and the file Devin leaves behind. Harnesses that announce nothing (GitHub Copilot, Aider, Windsurf, Warp) are not guessed at. `src/slackcli/signature.py` holds the table and is the one place to extend.

Signing turns a plain message into a section block plus the footer, so a signed message reads exactly like an unsigned one with a line of grey type under it. A body too long for a single section keeps its plain form and takes the footer as a trailing italic line instead.

### Workspaces

On Slack Enterprise Grid one token serves the grid org and the workspaces inside it, and each of them has its own host name. Add the extra names under `workspaces` so a single entry answers to all of them:

```toml
[orgs.acme]
token = "xoxp-your-user-token-here"
workspaces = ["acme-eng"]
```

With this entry, `--org=acme`, `--org=acme-eng`, `SLACK_ORG=acme-eng`, and a URL on either `acme.enterprise.slack.com` or `acme-eng.slack.com` all select the same token and the same cache. A workspace name must not repeat an org name, and two orgs must not claim the same workspace name.

### Token Types

- `xoxp-*` - User token (recommended, full visibility)
- `xoxb-*` - Bot token (limited to channels where bot is invited)

See [Creating a Slack API App](docs/creating-slack-api-app.md) for detailed setup instructions.

## Claude Code Plugin

A [Claude Code](https://docs.anthropic.com/en/docs/claude-code) plugin is bundled with this repository, providing a skill that teaches Claude how to use the `slack` CLI autonomously.

```bash
# Install the slackcli CLI (if not already)
uv tool install -e .

# Add the marketplace and install the plugin
claude plugin marketplace add fprochazka/slackcli
claude plugin install slackcli@fprochazka-slackcli
```

To upgrade after a new release:

```bash
claude plugin marketplace update fprochazka-slackcli
claude plugin update slackcli@fprochazka-slackcli
```

Once installed, the skill auto-approves read-only commands (`conversations list`, `messages list`, `search messages`, `users list`, `resolve`, etc.). Write operations (`messages send`, `messages edit`, `reactions add`, etc.) still require manual approval.

## Usage

### Global Options

```bash
slack --org=myworkspace <command>  # Use specific organization
slack --verbose <command>          # Enable debug logging
slack --help                       # Show help
```

### Environment Variables

| Variable | Description |
|----------|-------------|
| `SLACK_ORG` | Default organization name (alternative to `--org`) |
| `SLACK_CONFIG` | Path to config file (alternative to `--config`) |

```bash
# Using environment variables
export SLACK_ORG=myworkspace
slack conversations list

# Or inline
SLACK_ORG=myworkspace slack conversations list
```

### Conversations

```bash
# List all conversations (cached for 6 hours)
slack conversations list

# Filter by type
slack conversations list --public          # Public channels only
slack conversations list --private         # Private channels only
slack conversations list --dms             # DMs and group DMs only

# Filter by membership
slack conversations list --member          # Channels you're a member of
slack conversations list --non-member      # Channels you're not a member of

# Force refresh cache
slack conversations list --refresh
```

#### Membership

```bash
# Invite one or more users to a channel
slack conversations invite '#general' @john.doe
slack conversations invite '#general' @john.doe @jane@example.com U0123456789

# Continue inviting valid users when some IDs fail (already-in-channel, guest limits, etc.)
slack conversations invite '#general' @john @already-member --force

# JSON output (includes per-user errors[] on partial success)
slack conversations invite '#general' @john --json

# Join a public channel (private channels require an invite)
slack conversations join '#general'

# Leave a channel
slack conversations leave '#general'
```

`invite` resolves each user reference (`@handle`, email, or `U…` ID) to a user ID locally before calling the API, so unknown users fail fast with a clear error. Up to 1000 users per call.

### Messages

```bash
# List messages in a channel (default: last 25 messages, i.e. --tail 25)
slack messages list '#general'
slack messages list C0123456789

# Direction flags
slack messages list '#general' --tail 5                             # last 5 messages
slack messages list '#general' --head 5                             # first 5 in window
slack messages list '#general' --after 1234567890.123456            # messages after cursor (default 25, override with --head N)
slack messages list '#general' --before 1234567890.123456           # messages before cursor (default 25, override with --tail N)
slack messages list '#general' --after 1234567890.123456 --head 5   # next 5 newer
slack messages list '#general' --before 1234567890.123456 --tail 5  # previous 5 older

# Compose with time windows
slack messages list '#general' --today
slack messages list '#general' --last-7d
slack messages list '#general' --last-30d
slack messages list '#general' --since 2024-01-15
slack messages list '#general' --since 7d --until 3d
slack messages list '#general' --head 100 --since 2024-01-01 --until 2024-02-01

# Include thread replies inline
slack messages list '#general' --with-threads

# Show reactions
slack messages list '#general' --reactions=counts   # :+1: 3
slack messages list '#general' --reactions=names    # :+1: alice, bob

# View a specific thread
slack messages list '#general' 1234567890.123456
slack messages list '#general' 1234567890.123456 --tail 10  # last 10 replies;
                                                            # if the thread
                                                            # overflows, the
                                                            # root is shown as
                                                            # a placeholder
                                                            # line — use --head
                                                            # to see it.

# JSON output
slack messages list '#general' --json

# Pagination output: when more messages exist on either side of the returned
# slice, the text output prints a trailing footer such as
#     [older: --before 1234.5678 | newer: --after 2345.6789]
# and the JSON envelope includes `has_more_before`, `has_more_after`,
# `next_before_ts`, and `next_after_ts`. Use these cursors to page further.

# Send a message to a channel
slack messages send '#general' "Hello world"

# Send a direct message (DM)
slack messages send '@john.doe' "Hello via DM"
slack messages send '@john@example.com' "Hello by email"
slack messages send 'U0123456789' "Hello by user ID"

# Reply in a thread
slack messages send '#general' --thread 1234567890.123456 "Reply in thread"

# Read message from stdin
echo "Hello" | slack messages send '#general' --stdin

# Upload a file
slack messages send '#general' --file ./report.pdf

# Upload a file with a message
slack messages send '#general' "Here's the report" --file ./report.pdf

# Upload multiple files
slack messages send '#general' --file ./a.csv --file ./b.csv

# Edit an existing message
slack messages edit '#general' 1234567890.123456 "Updated message"

# Remove the link previews from a message, keeping its content
slack messages edit '#general' 1234567890.123456 --remove-link-previews

# Same for a thread reply (--thread locates it)
slack messages edit '#general' 1234567890.123456 --remove-link-previews --thread 1234567890.000001

# Change the text and remove the previews in one edit
slack messages edit '#general' 1234567890.123456 "Updated message" --remove-link-previews

# Delete a message (with confirmation prompt)
slack messages delete '#general' 1234567890.123456

# Skip confirmation
slack messages delete '#general' 1234567890.123456 --force
```

Messages with file attachments will show the file name, size, and download URL.

`--remove-link-previews` drops the previews Slack and other apps unfurled into a message. The message text is optional there: without it the message keeps the content it has and only loses the previews. The removal sticks — a link left in the text is not unfurled again — although a later edit that changes the links may produce a fresh preview. A preview an app posted (Linear, GitHub, ...) cannot be brought back at all: only deleting the message and posting it again restores it. Use `--thread <parent ts>` when the message is a thread reply and you are not passing new text, because a reply cannot be found without knowing its thread.

#### Message formatting

Message bodies are written in Markdown. When a body looks like Markdown, it is converted to Slack rich text before it is sent: `**bold**`, `~~strike~~`, `` `code` ``, `-` and `1.` lists (nested ones included), ```` ```sql ```` fences with syntax highlighting, `>` quotes, `[label](url)` links and `#` headings, which render bold. A Markdown table becomes a monospace block, because Slack has no table element, so its columns stay aligned.

```bash
# Write a multi-line body to a file and pipe it in; a "\n" inside a shell argument is not a newline
cat deploy-report.md | slack messages send '#general' --stdin
```

where `deploy-report.md` holds:

```markdown
## Deploy report

Shipped **v2.4.0**, see [the release](https://example.com/releases/2.4.0).

- migrations: none
- rollback: `git revert abc123`
```

Slack's own syntax keeps working inside a Markdown body, and is still the only way to write a mention: `<@U0123456789>`, `<#C0123456789|general>`, `<!here>`, `<!subteam^S0123456|@backend>`, `:white_check_mark:` and `<https://example.com|labelled link>`. Any other `<...>` form, such as `<#G0123456>` or `<!date^1234567890^{date}>`, is sent as literal text. Slack syntax inside a code fence or a backtick span is left exactly as written.

`--format` decides how the body is read:

| Value | Meaning |
|-------|---------|
| `auto` (default) | Markdown when the body carries a Markdown signal, otherwise sent as it stands |
| `markdown` | Always convert, even when the body has no obvious signal |
| `mrkdwn` | Never convert: the body goes out in Slack's own mrkdwn, exactly as today |

Detection only reacts to syntax the two dialects do not share, so a plain Slack message with `*bold*`, `_italic_` or `<url|label>` is never mistaken for Markdown. Mind the difference when you write one: `*x*` is bold in mrkdwn but italic in Markdown. `send`, `edit` and `scheduled create` all take `--format`, and report the path taken in `--json` as `"format": "markdown" | "mrkdwn" | "blocks"`. Anything the converter cannot express is what `--blocks` is for.

#### Sending rich blocks

```bash
# Post a Block Kit payload built by hand or by the Block Kit Builder
slack messages send '#general' --blocks ./blocks.json

# Read the blocks from stdin
cat blocks.json | slack messages send '#general' --blocks -

# Give the message its own fallback text (otherwise it is derived from the blocks)
slack messages send '#general' "Deploy report" --blocks ./blocks.json

# The same option works on edit and on scheduled create
slack messages edit '#general' 1234567890.123456 --blocks ./blocks.json
slack scheduled create '#general' "in 1h" --blocks ./blocks.json
```

The file holds either a JSON array of blocks or a Block Kit Builder export, an object with a `blocks` key. Slack's limits are checked before the call: at most 50 blocks, 3000 characters of text per `section` or `context` block, and roughly 12,000 characters of rich text per message. Every message that carries blocks also carries a fallback text, which is what Slack shows in notifications, in search results and in clients that cannot render blocks. `--blocks` is the escape hatch for hand-built payloads; a plain message body needs none of this.

#### Limits

A message is at most 4000 characters. Slack does not refuse a longer one on send: it silently splits it into several posts and reports only the last part's timestamp, and `messages edit` then rejects it outright. `send`, `edit` and `scheduled create` therefore check the length before calling the API and fail without posting anything, so split a long message yourself, for example into a thread.

### Reactions

```bash
# Add a reaction
slack reactions add '#general' 1234567890.123456 thumbsup
slack reactions add '#general' 1234567890.123456 :+1:  # Colons are stripped

# Remove a reaction
slack reactions remove '#general' 1234567890.123456 thumbsup
```

### Pins

```bash
# List pinned messages in a channel
slack pins list '#general'

# JSON output
slack pins list '#general' --json

# Pin a message
slack pins add '#general' 1234567890.123456

# Unpin a message
slack pins remove '#general' 1234567890.123456
```

### Scheduled Messages

```bash
# Schedule a message for a specific time
slack scheduled create '#general' "2025-02-03 09:00" "Good morning!"

# Schedule relative to now
slack scheduled create '#general' "in 1h" "Reminder"

# Schedule for tomorrow
slack scheduled create '#general' "tomorrow 9am" "Daily standup"

# List scheduled messages
slack scheduled list

# List scheduled messages for a specific channel
slack scheduled list '#general'

# Delete a scheduled message
slack scheduled delete '#general' <scheduled_message_id>
```

### Files

```bash
# Download by file ID
slack files download F0ABC123DEF

# Download by URL (from message output)
slack files download 'https://files.slack.com/files-pri/T0XXX-F0XXX/download/file.txt'

# JSON output
slack files download F0ABC123DEF --json
```

Files are downloaded to a unique directory `/tmp/slackcli-<random>/` using the original filename. The full path is printed after download.

### Search

```bash
# Search for messages
slack search messages "quarterly report"

# Filter by channel
slack search messages "bug fix" --in '#engineering'

# Filter by sender
slack search messages "deadline" --from '@john.doe'

# Filter by date
slack search messages "meeting" --after 7d
slack search messages "project" --before 2024-01-15 --after 2024-01-01

# Sort by timestamp instead of relevance
slack search messages "update" --sort timestamp --sort-dir desc

# Pagination
slack search messages "report" --limit 50 --page 2

# Search for files
slack search files "report.pdf"
slack search files "spreadsheet" --in '#finance'
slack search files "presentation" --from '@jane.doe'
slack search files "budget" --after 30d

# JSON output
slack search messages "test" --json
```

**Note:** Search requires the `search:read` OAuth scope. If you get a missing scope error, add this scope in your Slack app settings at https://api.slack.com/apps and reinstall the app.

### Users

```bash
# List all users
slack users list

# List users as JSON
slack users list --json

# Search for users
slack users search "john"

# Get user details by username
slack users get @john.doe

# Get user details by ID
slack users get U0123456789
```

### Utilities

```bash
# Resolve a Slack message URL to see its content
slack resolve 'https://myworkspace.slack.com/archives/C0123456789/p1234567890123456'

# Thread reply URL
slack resolve 'https://myworkspace.slack.com/archives/C0123456789/p1234567890123456?thread_ts=1234567890.123456'

# Channel URL - prints channel metadata, also for channels you are not a member of
slack resolve 'https://myworkspace.slack.com/archives/C0123456789'

# Legacy channel URL
slack resolve 'https://myworkspace.slack.com/messages/C0123456789'

# Web client URL (app.slack.com) - channel and thread
slack resolve 'https://app.slack.com/client/T0123456789/C0123456789'
slack resolve 'https://app.slack.com/client/T0123456789/C0123456789/thread/C0123456789-1234567890.123456'

# User profile URL
slack resolve 'https://myworkspace.slack.com/team/U0123456789'

# File URL
slack resolve 'https://myworkspace.slack.com/files/U0123456789/F0123456789/report.pdf'

# JSON output
slack resolve 'https://...' --json

# Show the current configuration
slack config
```

The `resolve` command extracts the workspace from the URL, so `--org` is optional. An `app.slack.com` URL names no workspace, so it uses the default org or `--org`.

The JSON output carries a `type` field with the value `message`, `conversation`, `user` or `file`.

## Output Formats

### Text Output (Default)

Human-readable format with resolved usernames and mentions:

```
2024-01-15 10:30:45  @john.doe
  Hello team, here's the update...
  [3 replies, thread_ts=1234567890.123456]
  :+1: 5 :heart: 2

2024-01-15 10:32:10  @jane.smith
  Thanks for sharing!
```

### JSON Output

Machine-readable format for AI agents:

```json
{
  "channel": "C0123456789",
  "channel_name": "general",
  "messages": [
    {
      "ts": "1234567890.123456",
      "user_id": "U0123456789",
      "user_name": "john.doe",
      "text": "Hello team...",
      "thread_ts": null,
      "reply_count": 3,
      "reactions": [{"name": "thumbsup", "count": 5, "users": ["alice", "bob"]}],
      "files": [
        {
          "id": "F0123456789",
          "name": "report.pdf",
          "size": 102400,
          "url_private_download": "https://files.slack.com/..."
        }
      ],
      "attachments": [
        {
          "id": 1,
          "from_url": "https://linear.app/acme/issue/ENG-1",
          "is_app_unfurl": true,
          "app_id": "A0123456789",
          "service_name": "Linear",
          "title": "ENG-1 Fix the thing",
          "title_link": "https://linear.app/acme/issue/ENG-1",
          "text": "Reported by @alice"
        }
      ]
    }
  ]
}
```

`text` is what the author wrote. `attachments` holds the link previews Slack and other apps added to the message, which nobody typed. Text output still prints the two together, so a message reads the same as in Slack.

## Developing

### Setup

```bash
# Clone and install dependencies
git clone <repo>
cd slackcli
uv sync
```

### Running

```bash
# Run the CLI
uv run slack --help

# Run a command
uv run slack --org=myworkspace conversations list
```

### Linting and Formatting

```bash
# Format code
uv run ruff format .

# Lint code
uv run ruff check .

# Lint and auto-fix
uv run ruff check --fix .
```

### Project Structure

```
src/slackcli/
├── __init__.py         # Package version
├── cli.py              # Main CLI entry point
├── client.py           # SlackCli class (API access)
├── config.py           # Configuration loading
├── context.py          # CLI context (org, token)
├── cache.py            # Cache utilities
├── models.py           # Data classes
├── output.py           # Output formatting
├── users.py            # User info resolution
├── blocks.py           # Block Kit rendering
├── logging.py          # Logging setup
├── errors.py           # Custom exceptions
├── retry.py            # Retry utilities for API calls
└── commands/
    ├── conversations.py  # Conversation list/filter
    ├── messages.py       # List, send, edit, delete messages
    ├── reactions.py      # Add/remove reactions
    ├── pins.py           # List, add, remove pins
    ├── scheduled.py      # List, create, delete scheduled messages
    ├── search.py         # Search messages and files
    ├── files.py          # Download files
    ├── users.py          # List, search, get users
    └── resolve.py        # URL resolution
```

## Releasing

1. Bump the version in **both** plugin manifest files so they stay in lockstep:
   - `.claude-plugin/marketplace.json`
   - `coding-agent-plugins/claude-code/.claude-plugin/plugin.json`

2. Commit and push the bump.

3. **Wait for CI to pass on `master`** — never tag a red build.

4. Review changes since the last release and draft release notes:

   ```bash
   git log $(git describe --tags --abbrev=0)..HEAD --oneline
   ```

5. Tag and push:

   ```bash
   git tag -a v<version> -F /tmp/release-notes.md
   git push origin v<version>
   ```

6. Create the GitHub release:

   ```bash
   gh release create v<version> -F /tmp/release-notes.md --title v<version>
   ```

## License

MIT
