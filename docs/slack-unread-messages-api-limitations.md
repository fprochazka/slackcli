# Slack Unread Messages API Limitations

This document explains why a reliable "unread messages" feature cannot be implemented using the official Slack API.

## Summary

The Slack API has **significant limitations** for retrieving unread message counts. The `unread_count`, `unread_count_display`, and `last_read` fields are **only reliably available for direct message (DM) conversations**, not for public or private channels.

## API Methods Investigated

### conversations.info

- Returns conversation details including `unread_count`, `unread_count_display`, and `last_read` fields
- **Critical limitation**: These fields are **only included for DM conversations**, not for public or private channels
- Documentation states these fields appear "when the calling user is a channel member" but this is inconsistent for non-DM channels

### conversations.list

- Returns a list of conversations
- Returns "limited channel-like conversation objects"
- Does **not** include `unread_count` or `last_read` fields
- Must call `conversations.info` for each channel to get detailed info

### users.conversations

- Lists conversations for a user
- Same limitation: does not include unread count information directly
- Requires individual `conversations.info` calls for each channel

## What Works vs What Doesn't

| Conversation Type | `unread_count` available? | `last_read` available? |
|-------------------|---------------------------|------------------------|
| 1-on-1 DMs        | Yes                       | Yes                    |
| Group DMs (MPIMs) | Inconsistent              | Inconsistent           |
| Public channels   | No                        | No (or inconsistent)   |
| Private channels  | No                        | No (or inconsistent)   |

## Token Type Requirements

- **User tokens (`xoxp-*`)**: Required for accessing any unread count information. User tokens represent a specific user's context and read state.
- **Bot tokens (`xoxb-*`)**: Cannot access unread counts because bots don't have a personal "read/unread" state.

## Workarounds Considered

### Manual Calculation (Not Reliable)

The theoretical workaround is:
1. Call `conversations.info` to get `last_read` timestamp
2. Call `conversations.history` to fetch messages since that timestamp
3. Count messages where `ts > last_read`

However, this doesn't work because `last_read` is not reliably returned for channels.

### Undocumented Internal API

Slack's web client uses an undocumented internal API: `https://slack.com/api/client.counts`

This endpoint returns unread counts for all channels, DMs, and threads. However:
- **Not officially supported** by Slack
- May break without notice
- Not suitable for production use

## Historical Context

- The `unread_count` and `unread_count_display` fields were removed from `rtm.start` responses in 2017
- Legacy methods like `channels.info` and `groups.info` (which had an `unreads` parameter) were deprecated in favor of the Conversations API
- The Conversations API intentionally limits unread counts to DM conversations only

## Conclusion

There is no reliable way to implement an "unread messages" feature for channels using the official Slack API. This is a deliberate limitation in Slack's API design.

If unread tracking is essential, the only options are:
1. Limit functionality to DMs only (where the API works)
2. Use the undocumented `client.counts` API (not recommended, may break)
3. Build custom tracking by storing message timestamps and comparing (requires persistent storage)

## References

- [conversations.info method | Slack Developer Docs](https://docs.slack.dev/reference/methods/conversations.info/)
- [conversations.list method | Slack Developer Docs](https://docs.slack.dev/reference/methods/conversations.list/)
- [Conversation object | Slack Developer Docs](https://docs.slack.dev/reference/objects/conversation-object/)
- [Retrieving messages | Slack Developer Docs](https://docs.slack.dev/messaging/retrieving-messages/)
