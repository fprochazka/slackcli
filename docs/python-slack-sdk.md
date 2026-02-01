# Python Slack SDK Reference

## Installation

```bash
pip install slack-sdk
```

## Client Initialization

### WebClient (Synchronous)

```python
from slack_sdk import WebClient

client = WebClient(
    token="xoxp-...",                    # Required: Bot or user token
    base_url="https://slack.com/api/",   # Default API base URL
    timeout=30,                          # Request timeout in seconds
    proxy=None,                          # Proxy URL string
    headers=None,                        # Additional headers dict
    retry_handlers=None,                 # Custom retry handlers
)
```

### AsyncWebClient (Asynchronous)

```python
from slack_sdk.web.async_client import AsyncWebClient

client = AsyncWebClient(
    token="xoxp-...",
    timeout=30,
    # Same options as WebClient
)
```

## Common Operations

### List Conversations

```python
# Get all conversations (with pagination)
for page in client.conversations_list(types="public_channel,private_channel,im,mpim"):
    for channel in page["channels"]:
        print(f"{channel['id']}: {channel.get('name', 'DM')}")
```

### Fetch Message History

```python
from datetime import datetime, timedelta

# Last 7 days
oldest = (datetime.now() - timedelta(days=7)).timestamp()

response = client.conversations_history(
    channel="C123456789",
    oldest=str(oldest),
    limit=100
)

for msg in response["messages"]:
    print(f"{msg['ts']}: {msg.get('text', '')}")
```

### Fetch Thread Replies

```python
response = client.conversations_replies(
    channel="C123456789",
    ts="1769420875.054379",  # Parent message timestamp
    limit=100
)

# First message is the parent
parent = response["messages"][0]
replies = response["messages"][1:]
```

### Get User Info

```python
response = client.users_info(user="U123456789")
user = response["user"]
print(f"Name: {user['real_name']}")
print(f"Username: {user['name']}")
print(f"Email: {user['profile'].get('email', 'N/A')}")
```

### Get Channel Info

```python
response = client.conversations_info(channel="C123456789")
channel = response["channel"]
print(f"Name: {channel['name']}")
print(f"Topic: {channel.get('topic', {}).get('value', '')}")
```

## Pagination

The SDK handles cursor-based pagination automatically:

```python
# Automatic pagination with iterator
messages = []
for page in client.conversations_history(channel="C123", limit=100):
    messages.extend(page["messages"])
    if len(messages) >= 500:
        break

# Manual pagination
response = client.conversations_history(channel="C123", limit=100)
messages = response["messages"]

while response.get("has_more"):
    cursor = response["response_metadata"]["next_cursor"]
    response = client.conversations_history(
        channel="C123",
        limit=100,
        cursor=cursor
    )
    messages.extend(response["messages"])
```

## Error Handling

```python
from slack_sdk.errors import SlackApiError

try:
    response = client.conversations_history(channel="C123")
except SlackApiError as e:
    print(f"Error: {e.response['error']}")
    # Common errors:
    # - channel_not_found
    # - not_in_channel
    # - ratelimited
    # - invalid_auth
```

## Retry Handlers

```python
from slack_sdk.http_retry import (
    RateLimitErrorRetryHandler,
    ConnectionErrorRetryHandler,
)

client = WebClient(
    token="xoxp-...",
    retry_handlers=[
        RateLimitErrorRetryHandler(max_retry_count=3),
        ConnectionErrorRetryHandler(max_retry_count=2),
    ]
)
```

## Available Clients

| Client | Purpose |
|--------|---------|
| `WebClient` | Main HTTP API client |
| `AsyncWebClient` | Async version of WebClient |
| `WebhookClient` | Incoming webhooks |
| `SocketModeClient` | WebSocket real-time connections |
| `AuditLogsClient` | Enterprise audit logs |
| `SCIMClient` | User provisioning (Enterprise) |

## SDK Structure

```
slack_sdk/
├── web/
│   ├── client.py         # WebClient
│   ├── async_client.py   # AsyncWebClient
│   └── slack_response.py # Response wrapper
├── webhook/
│   └── client.py         # WebhookClient
├── socket_mode/
│   └── client.py         # SocketModeClient
├── oauth/
│   └── installation_store/
├── errors/
│   └── __init__.py       # SlackApiError, etc.
└── http_retry/
    └── handler.py        # Retry handlers
```

## Method Naming Convention

Python methods map to API endpoints:

| Python Method | API Endpoint |
|---------------|--------------|
| `chat_postMessage()` | `chat.postMessage` |
| `conversations_history()` | `conversations.history` |
| `users_info()` | `users.info` |
| `admin_users_list()` | `admin.users.list` |

## Response Object

`SlackResponse` provides dict-like access:

```python
response = client.users_info(user="U123")

# Dict-like access
user = response["user"]
ok = response["ok"]

# Get with default
email = response.get("user", {}).get("profile", {}).get("email")

# Raw data
raw = response.data
```
