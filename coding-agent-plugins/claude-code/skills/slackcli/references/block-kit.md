# Block Kit payloads with `--blocks`

`--blocks` posts a hand-built Block Kit payload instead of a message body. Reach for it only when a layout cannot be expressed in Markdown — a `divider`, a `context` line, an `image` block, a `section` with `fields`, buttons. For ordinary messages write Markdown; the CLI converts it to the same `rich_text` blocks (see "Message Formatting" in SKILL.md).

## Usage

```bash
slack messages send '#channel' --blocks ./blocks.json            # from a file
cat blocks.json | slack messages send '#channel' --blocks -      # from stdin
slack messages send '#channel' "Fallback text" --blocks ./blocks.json
slack messages edit '#channel' 1234567890.123456 --blocks ./blocks.json
slack scheduled create '#channel' "in 1h" --blocks ./blocks.json
```

`--blocks -` and `--stdin` cannot be combined; both read stdin.

## Accepted input

- A JSON array of blocks: `[{"type": "section", ...}, ...]`
- A Block Kit Builder export: an object with a `blocks` key

Anything else is rejected before the API call with a message naming the problem.

## Fallback text

A message that carries blocks always carries a plain `text` as well — Slack shows it in notifications, search results and clients that cannot render blocks.

- With a message argument, that argument is the fallback. It is rejected over 4000 characters (Slack's cap for message text), never shortened, because it is text somebody typed.
- Without one, the fallback is rendered from the blocks and shortened to 4000 characters if needed.

## Limits, checked before the API call

| Limit | Value |
|---|---|
| blocks per message | 50 (the agent signature footer counts as one) |
| `section` / `context` text | 3000 characters |
| rich text per message, across all `rich_text` blocks | ~12,000 characters (Slack refuses at ~13,000) |
| fallback text | 4000 characters |

A payload over a limit is refused locally; split it into several messages.

## Interaction with other flags

- `--blocks` wins over `--format`; the caller built the content.
- The agent signature (see SKILL.md) is appended as a `context` block, unless `agent_signature = "off"`.
- `edit --blocks --remove-link-previews` replaces the content and drops the previews in one call.
- `--json` output reports `"format": "blocks"`.

## Example payload

Syntax-highlighted code with a footer — the shape the Markdown converter produces for a fenced block:

```json
[
  {
    "type": "rich_text",
    "elements": [
      {
        "type": "rich_text_section",
        "elements": [{"type": "text", "text": "Nightly order count:"}]
      },
      {
        "type": "rich_text_preformatted",
        "language": "sql",
        "elements": [{"type": "text", "text": "select count(*)\nfrom orders\nwhere created_at > now() - interval '1 day';"}]
      }
    ]
  },
  {
    "type": "context",
    "elements": [{"type": "mrkdwn", "text": "Runs at 06:00 UTC"}]
  }
]
```

`rich_text_preformatted` accepts any `language` string; unknown ones render without highlighting. Slack's element reference: https://api.slack.com/reference/block-kit/blocks#rich_text
