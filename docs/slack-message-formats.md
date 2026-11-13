# Slack Message Formats

## Mention Macros

Slack uses special macro syntax in message text:

| Macro | Description | Example |
|-------|-------------|---------|
| `<@U123>` | User mention | `<@U0123456789>` |
| `<#C123>` | Channel mention | `<#C0123456789>` |
| `<#C123\|name>` | Channel with name | `<#C0123456789\|general>` |
| `<!here>` | @here mention | |
| `<!channel>` | @channel mention | |
| `<!everyone>` | @everyone mention | |
| `<!subteam^S123\|@name>` | User group | `<!subteam^S123\|@devops>` |
| `<url>` | Plain URL | `<https://example.com>` |
| `<url\|text>` | URL with label | `<https://example.com\|Click here>` |

## Block Kit

Rich messages use Block Kit - a JSON-based UI framework.

### Block Types

| Type | Description |
|------|-------------|
| `section` | Text block with optional accessory |
| `context` | Secondary text/images |
| `header` | Large bold text |
| `divider` | Horizontal line |
| `image` | Image block |
| `actions` | Interactive elements (buttons, selects) |
| `rich_text` | Complex formatted text |

### Section Block

```json
{
  "type": "section",
  "text": {
    "type": "mrkdwn",
    "text": "Hello *world*"
  }
}
```

### Rich Text Block

Complex nested structure for formatted text:

```json
{
  "type": "rich_text",
  "elements": [
    {
      "type": "rich_text_section",
      "elements": [
        {"type": "text", "text": "Hello "},
        {"type": "user", "user_id": "U123"},
        {"type": "emoji", "name": "wave"}
      ]
    }
  ]
}
```

### Rich Text Element Types

| Type | Description |
|------|-------------|
| `rich_text_section` | Paragraph |
| `rich_text_list` | Bulleted/numbered list |
| `rich_text_quote` | Block quote |
| `rich_text_preformatted` | Code block |

### Rich Text Inline Elements

| Type | Fields | Renders As |
|------|--------|------------|
| `text` | `text`, `style` | Plain/styled text |
| `user` | `user_id` | @username |
| `channel` | `channel_id` | #channel |
| `link` | `url`, `text` | Hyperlink |
| `emoji` | `name` | :emoji: |
| `broadcast` | `range` | @here/@channel |

## Attachments (Legacy)

Older format still used by many integrations:

```json
{
  "attachments": [
    {
      "fallback": "Plain text summary",
      "color": "#36a64f",
      "title": "Title",
      "title_link": "https://example.com",
      "text": "Attachment text",
      "fields": [
        {"title": "Status", "value": "Open", "short": true}
      ]
    }
  ]
}
```

### Attachment Fields

| Field | Description |
|-------|-------------|
| `fallback` | Plain text fallback |
| `color` | Sidebar color |
| `title` | Title text |
| `title_link` | Title URL |
| `author_name` | Author display name |
| `text` | Main text |
| `fields` | Key-value pairs |
| `blocks` | Nested Block Kit blocks |

## Message URL Format

### Regular Message

```
https://{workspace}.slack.com/archives/{channel_id}/p{timestamp_no_dot}
```

Example:
```
https://myworkspace.slack.com/archives/C0123456789/p1234567892345678
```

- `C0123456789` = channel ID
- `p1234567892345678` = timestamp with dot removed (`1234567892.345678`)

### Thread Reply

```
https://{workspace}.slack.com/archives/{channel_id}/p{message_ts}?thread_ts={parent_ts}&cid={channel_id}
```

Example:
```
https://myworkspace.slack.com/archives/C0123456789/p1234567891234567?thread_ts=1234567890.123456&cid=C0123456789
```

### URL Parsing

To convert URL timestamp to API timestamp:

```python
def url_ts_to_api_ts(url_ts: str) -> str:
    """Convert p1234567892345678 to 1234567892.345678"""
    ts = url_ts.lstrip('p')
    # Insert dot before last 6 digits
    return f"{ts[:-6]}.{ts[-6:]}"
```
