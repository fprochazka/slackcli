# Slack API Overview

## Token Types

| Token Type | Prefix | Description | Use Case |
|------------|--------|-------------|----------|
| **User Token** | `xoxp-` | Acts as the user | Full visibility, true impersonation |
| **Bot Token** | `xoxb-` | Acts as a bot app | Limited to invited channels, has "APP" tag |
| **App Token** | `xapp-` | App-level token | Socket Mode WebSocket connections only |

### User Tokens (xoxp-)
- Inherits user's entire visibility (private channels, DMs)
- Messages appear as the user (no "APP" tag)
- Required for personal assistant use cases
- Obtained via OAuth 2.0 flow with user scopes

### Bot Tokens (xoxb-)
- Can only see channels where bot is invited
- Cannot see user's DMs or private channels without invite
- Messages have "APP" tag
- Simpler to set up but limited visibility

## API Namespaces

### chat.* - Message Operations

Methods for working with **message content**:

| Method | Description |
|--------|-------------|
| `chat.postMessage` | Send a message to a channel |
| `chat.postEphemeral` | Send ephemeral message (visible to one user) |
| `chat.update` | Update an existing message |
| `chat.delete` | Delete a message |
| `chat.scheduleMessage` | Schedule message for future delivery |
| `chat.getPermalink` | Get permanent URL for a message |

### conversations.* - Channel Management

Methods for managing **channels and conversation containers**:

| Method | Description |
|--------|-------------|
| `conversations.list` | List all conversations |
| `conversations.info` | Get channel metadata |
| `conversations.history` | Fetch message history (parent messages) |
| `conversations.replies` | Fetch thread replies |
| `conversations.members` | List channel members |
| `conversations.create` | Create a new channel |
| `conversations.join` | Join a channel |
| `conversations.invite` | Invite users to a channel |
| `conversations.archive` | Archive a channel |

### Key Distinction

- `chat.*` = Message operations (the **content**)
- `conversations.*` = Channel operations (the **container**)

The Conversations API replaced the legacy `channels.*`, `groups.*`, `im.*`, and `mpim.*` APIs (deprecated Feb 2021).

## Conversation Types

| Type | Description | ID Prefix |
|------|-------------|-----------|
| `public_channel` | Public channel | `C` |
| `private_channel` | Private channel | `C` or `G` |
| `im` | Direct message (1:1) | `D` |
| `mpim` | Multi-person DM (group DM) | `G` |

## Message Structure

### Core Fields

| Field | Type | Description |
|-------|------|-------------|
| `ts` | string | Message timestamp/ID (e.g., `"1610144875.000600"`) |
| `user` | string | User ID of sender |
| `text` | string | Message text content |
| `thread_ts` | string | Thread parent timestamp (if in thread) |
| `reply_count` | int | Number of replies (parent messages only) |
| `reactions` | array | Emoji reactions |
| `blocks` | array | Block Kit blocks (rich content) |
| `attachments` | array | Legacy attachments |

### Thread Detection

- **Thread parent**: `thread_ts` is present AND `thread_ts == ts`
- **Thread reply**: `thread_ts` is present AND `thread_ts != ts`

### Reactions Structure

```json
{
  "reactions": [
    {
      "name": "thumbsup",
      "count": 3,
      "users": ["U123", "U456"]
    }
  ]
}
```

Note: The `users` array may be truncated. Use `count` for accurate total.

## Timestamps

Slack uses a unique timestamp format: `"seconds.microseconds"` (e.g., `"1610144875.000600"`).

### Conversion

```python
from datetime import datetime

def datetime_to_slack_ts(dt: datetime) -> str:
    return f"{dt.timestamp():.6f}"

def slack_ts_to_datetime(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts))
```

## Pagination

The API uses cursor-based pagination:

```python
response = client.conversations_history(channel="C123", limit=100)
# response["has_more"] = True
# response["response_metadata"]["next_cursor"] = "..."

# The SDK handles pagination automatically:
for page in client.conversations_history(channel="C123", limit=100):
    messages = page["messages"]
```

## Rate Limits

As of 2025, many endpoints are Tier 1 for non-Marketplace apps (~1 request/minute).

Affected endpoints:
- `conversations.history`
- `conversations.replies`

Mitigation strategies:
- Cache aggressively
- Use incremental fetches with `oldest` parameter
- Implement exponential backoff
