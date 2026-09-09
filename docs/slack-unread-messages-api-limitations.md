# Slack Unread State: What the API Can and Cannot Do

This document explains what unread information the Slack API exposes to a user token (`xoxp-*`), and why a channel unread *count* is harder to get than it looks. It is written for anyone considering an unread feature in this CLI. The findings below were verified against a live Enterprise Grid workspace with an `xoxp-*` user token.

## Summary

Three separate things are often lumped together as "unread support". They have very different answers:

- **Setting read/unread state** works fully on a user token. `conversations.mark` is a documented public method. Moving the read cursor is how both "mark read" and "mark unread" are implemented.
- **Reading the per-channel read cursor** (`last_read`) works on a user token, for channels you are a member of. `conversations.info` returns it.
- **Reading a per-channel unread *count* in one field** does not work on a user token. `unread_count` and `unread_count_display` are returned for DMs only, not for public or private channels.

So an unread count for a channel is not handed to you, but you can compute it: read `last_read`, then count the messages after it. The only one-call source of counts across all channels is the internal `client.counts` endpoint, which a user token cannot call.

## Setting read and unread state

There is no separate "mark unread" method. Both directions are the same call, `conversations.mark`, differing only in the `ts` you send. The method sets the conversation's `last_read` cursor; every message strictly newer than `ts` becomes unread.

- Mark read: send the `ts` of the newest message in the channel.
- Mark unread from message M: send the `ts` of the message *immediately before* M. Everything from M onward is then unread. This off-by-one is the only subtle part, and it costs one extra `conversations.history` call to resolve M's predecessor.

`conversations.mark` is documented and goes through the normal OAuth scope check. It needs one write scope per conversation type: `channels:write` (public), `groups:write` (private), `im:write` (DMs), `mpim:write` (group DMs). A CLI that marks any conversation wants all four.

Threads keep their own cursor. The web client uses `subscriptions.thread.mark` (`channel`, `thread_ts`, `ts`, `read=1`) for it. That method is undocumented and internal, though it accepts the same public write scopes. `conversations.mark` does not touch thread cursors.

## Reading the read cursor

`conversations.info` returns `last_read` for a channel you are a member of, on a user token. This contradicts older belief that `last_read` is DM-only; it is not. What it does *not* return for a channel is `unread_count` or `unread_count_display` — both come back null.

For DMs, `conversations.info` returns all three: `last_read`, `unread_count`, and `unread_count_display`.

Caveat: this was verified on member channels on one Grid workspace. A channel you are not a member of has no personal read state to return, so `last_read` is only meaningful for member conversations.

## Computing an unread count for a channel

Because `last_read` is available, the manual calculation that older notes dismissed as impossible actually works:

1. `conversations.info(channel)` gives `last_read`.
2. `conversations.history(channel, oldest=last_read, inclusive=false, limit=…)` returns the messages after the cursor. Count them, paging on `has_more` if needed.

This was checked against `client.counts` on a channel with a real backlog: the manual count matched, and the `last_read` values from the two sources were identical. The cost is at least two API calls per channel, plus one more per extra page of history, so it does not scale to "unread across the whole workspace" without many calls and rate-limit pressure.

The old shortcut for this — `conversations.history` with `unreads=true`, which reportedly returned `unread_count_display` around 2021 — no longer works. The parameter is accepted, the call succeeds, and no unread field comes back. Do not rely on it.

## The one-call source: client.counts

The Slack web client reads unread state from `client.counts`. One call returns, for every channel, DM, and group DM, an entry of `{id, last_read, latest, updated, mention_count, has_unreads}`, plus `threads.unread_count_by_channel`. It is the only way to get the whole workspace's unread state in a single round trip.

It is not usable from this CLI's normal token. Two reasons:

- **Token type.** `client.counts` rejects an `xoxp-*` token with `not_allowed_token_type`. Its accepted scopes are `rtm:stream,client` — the internal `client` scope granted to the web client, not to third-party apps. It works only with a browser session token (`xoxc-*`) plus the `d=xoxd-*` cookie.
- **It gives `has_unreads`, not a count.** For channels, the entry carries a boolean, not the number of unread messages. To turn that into a count you are back to the manual calculation above. Only threads get an actual `unread_count_by_channel` number.

### Why an xoxc session token is a bad foundation

Using a browser session token to reach `client.counts` (or the other internal endpoints: `conversations.view`, `client.userBoot`) carries risks a documented user token does not:

- **Unsupported by Slack.** Slack has stated that `xoxc` token use "is not supported or recommended" and that API methods are meant for bot or user tokens. The undocumented internal endpoints can change or break without notice.
- **Anomaly detection on Enterprise Grid.** Grid ships Anomaly Event Response, and some anomaly types are on by default. A non-browser client calling internal endpoints can trip `user_agent`, `unexpected_client`, or `unexpected_scraping` anomalies. A trip can auto-terminate the user's sessions and email the org owner and security admins, with the anomaly attributed to the named user. On an employer-owned workspace this is the material risk: in the audit log it looks the same as a stolen session cookie in use.
- **Fragility.** An `xoxc` token lives only as long as the browser session. Grid admins can set a session-duration policy as short as 8 hours, and tokens have been reported to rotate within hours. Re-extraction is manual (Chrome DevTools, or reading the desktop app's cookie store) and the extraction paths break across Slack releases.

## What works vs. what does not, on a user token

| Capability | Public channels | Private channels | Group DMs | 1-on-1 DMs |
|---|---|---|---|---|
| Set read/unread (`conversations.mark`) | Yes | Yes | Yes | Yes |
| Read `last_read` (`conversations.info`) | Yes (member) | Yes (member) | Yes | Yes |
| Read `unread_count` in one field | No | No | No | Yes |
| Compute unread count (`last_read` + history) | Yes | Yes | Yes | Yes |
| One-call counts across all conversations | No (needs `client.counts`, internal) | No | No | No |

## Token type requirements

- **User tokens (`xoxp-*`)**: required for any personal read state. A bot token has no personal read/unread state.
- **Bot tokens (`xoxb-*`)**: cannot access read cursors or unread counts.

## Conclusion

An unread feature for this CLI is feasible on a plain user token, within limits:

- Marking conversations read or unread is fully supported.
- Reading the read cursor and computing an unread count per channel works, at a cost of two or more API calls per channel.
- A single-call unread overview of the whole workspace is not available on a user token; the only source is the internal `client.counts`, and reaching it needs a browser session token whose cost and risk are covered above.

## References

- [conversations.mark method | Slack Developer Docs](https://docs.slack.dev/reference/methods/conversations.mark/)
- [conversations.info method | Slack Developer Docs](https://docs.slack.dev/reference/methods/conversations.info/)
- [conversations.history method | Slack Developer Docs](https://docs.slack.dev/reference/methods/conversations.history/)
- [Audit Logs anomaly reference | Slack Developer Docs](https://docs.slack.dev/reference/audit-logs-api/anomalous-events-reference/)
- [Slack API Terms of Service](https://slack.com/terms-of-service/api)
- [admin.users.session.setSettings method | Slack Developer Docs](https://docs.slack.dev/reference/methods/admin.users.session.setSettings/)
