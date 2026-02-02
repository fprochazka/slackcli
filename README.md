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
- Adding and removing reactions
- Automating channel exploration
- Integrating Slack into AI agents and scripts
- Quick API interactions from the terminal

## Installation

First clone the repository, then:

```bash
# Install with pipx (recommended)
pipx install -e -f .

# Or install with uv
uv tool install .
```

## Configuration

Create a configuration file at `~/.config/slackcli/config.toml`:

```toml
# Default organization (optional)
default_org = "myworkspace"

[orgs.myworkspace]
token = "xoxp-your-user-token-here"

[orgs.another-workspace]
token = "xoxp-another-token"
```

### Token Types

- `xoxp-*` - User token (recommended, full visibility)
- `xoxb-*` - Bot token (limited to channels where bot is invited)

See [Creating a Slack API App](docs/creating-slack-api-app.md) for detailed setup instructions.

## Usage

### Global Options

```bash
slack --org=myworkspace <command>  # Use specific organization
slack --verbose <command>          # Enable debug logging
slack --help                       # Show help
```

### List Conversations

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

### List Messages

```bash
# List messages in a channel (default: last 30 days)
slack messages '#general'
slack messages C0123456789

# Time filters
slack messages '#general' --today
slack messages '#general' --last-7d
slack messages '#general' --last-30d
slack messages '#general' --since 2024-01-15
slack messages '#general' --since 7d --until 3d

# Include thread replies inline
slack messages '#general' --with-threads

# Show reactions
slack messages '#general' --reactions=counts   # :+1: 3
slack messages '#general' --reactions=names    # :+1: alice, bob

# View a specific thread
slack messages '#general' 1234567890.123456

# JSON output
slack messages '#general' --json
```

### Resolve Message URLs

```bash
# Resolve a Slack message URL to see its content
slack resolve 'https://myworkspace.slack.com/archives/C0123456789/p1234567890123456'

# Thread reply URL
slack resolve 'https://myworkspace.slack.com/archives/C0123456789/p1234567890123456?thread_ts=1234567890.123456'

# JSON output
slack resolve 'https://...' --json
```

The `resolve` command extracts the workspace from the URL, so `--org` is optional.

### Send Messages

```bash
# Send a message to a channel
slack send '#general' "Hello world"

# Reply in a thread
slack send '#general' --thread 1234567890.123456 "Reply in thread"

# Read message from stdin
echo "Hello" | slack send '#general' --stdin

# JSON output (returns message timestamp)
slack send '#general' "Message" --json
```

### Edit Messages

```bash
# Edit an existing message
slack edit '#general' 1234567890.123456 "Updated message"

# JSON output
slack edit '#general' 1234567890.123456 "Updated" --json
```

### Delete Messages

```bash
# Delete a message (with confirmation prompt)
slack delete '#general' 1234567890.123456

# Skip confirmation
slack delete '#general' 1234567890.123456 --force
```

### Add/Remove Reactions

```bash
# Add a reaction
slack react '#general' 1234567890.123456 thumbsup
slack react '#general' 1234567890.123456 :+1:  # Colons are stripped

# Remove a reaction
slack unreact '#general' 1234567890.123456 thumbsup
```

### Show Configuration

```bash
slack config
```

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
      "reactions": [{"name": "thumbsup", "count": 5, "users": ["alice", "bob"]}]
    }
  ]
}
```

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
└── commands/
    ├── conversations.py  # Conversation commands
    ├── messages.py       # Message commands
    ├── resolve.py        # URL resolution
    ├── send.py           # Send messages
    ├── edit.py           # Edit messages
    ├── delete.py         # Delete messages
    └── react.py          # Add/remove reactions
```

## License

MIT
