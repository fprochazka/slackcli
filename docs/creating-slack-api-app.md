# Creating a Slack API App

This guide walks you through creating a Slack app to get a User OAuth Token (`xoxp-`) for use with slackcli.

## Step 1: Create the App

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App**
3. Select **From Scratch**
4. Enter an **App Name** (e.g., "My CLI Assistant")
5. Select your **Workspace**
6. Click **Create App**

## Step 2: Configure Display Information (Optional)

In **Basic Information** → **Display Information**, you can customize:

- **App name**: Display name for the app
- **Short description**: Brief description shown in the app directory
- **Long description**: Detailed description of what the app does
- **App icon**: Upload a custom icon

## Step 3: Configure OAuth Scopes

Navigate to **OAuth & Permissions** → **Scopes** → **User Token Scopes**.

### Minimal Scopes (Read-Only)

For basic read-only access to messages and channels:

| Scope | Description |
|-------|-------------|
| `channels:history` | View messages in public channels |
| `channels:read` | View basic info about public channels |
| `groups:history` | View messages in private channels |
| `groups:read` | View basic info about private channels |
| `im:history` | View messages in direct messages |
| `im:read` | View basic info about direct messages |
| `mpim:history` | View messages in group direct messages |
| `mpim:read` | View basic info about group direct messages |
| `users:read` | View people in workspace |
| `reactions:read` | View emoji reactions |

### Full Scopes (Read + Write)

For complete access including sending messages:

| Scope | Description |
|-------|-------------|
| `bookmarks:read` | List bookmarks |
| `bookmarks:write` | Create, edit, and remove bookmarks |
| `calls:read` | View information about calls |
| `canvases:read` | Access canvases and comments |
| `canvases:write` | Create, edit and remove canvases |
| `channels:history` | View messages in public channels |
| `channels:read` | View basic info about public channels |
| `channels:write` | Manage public channels |
| `channels:write.invites` | Invite members to public channels |
| `channels:write.topic` | Set public channel descriptions |
| `chat:write` | Send messages on user's behalf |
| `dnd:read` | View Do Not Disturb settings |
| `dnd:write` | Edit Do Not Disturb settings |
| `files:read` | View files in channels |
| `files:write` | Upload, edit, and delete files |
| `groups:history` | View messages in private channels |
| `groups:read` | View basic info about private channels |
| `groups:write` | Manage private channels |
| `groups:write.invites` | Invite members to private channels |
| `groups:write.topic` | Set private channel descriptions |
| `im:history` | View messages in direct messages |
| `im:read` | View basic info about direct messages |
| `im:write` | Start direct messages |
| `im:write.topic` | Set description in direct messages |
| `links:read` | View URLs in messages |
| `links:write` | Show previews of URLs |
| `lists:read` | Access lists and comments |
| `lists:write` | Create, edit and remove lists |
| `mpim:history` | View messages in group DMs |
| `mpim:read` | View basic info about group DMs |
| `mpim:write` | Start group direct messages |
| `mpim:write.topic` | Set description in group DMs |
| `pins:read` | View pinned content |
| `pins:write` | Add and remove pinned messages |
| `reactions:read` | View emoji reactions |
| `reactions:write` | Add and edit emoji reactions |
| `reminders:read` | View reminders |
| `reminders:write` | Manage reminders |
| `stars:read` | View starred messages |
| `stars:write` | Add or remove stars |
| `team:read` | View workspace info |
| `usergroups:read` | View user groups |
| `usergroups:write` | Create and manage user groups |
| `users.profile:read` | View profile details |
| `users:read` | View people in workspace |
| `users:read.email` | View email addresses |

## Step 4: Install the App

1. Navigate to **Install App** in the sidebar
2. Click **Install to Workspace**
3. Review the permissions and click **Allow**
4. Copy the **User OAuth Token** (starts with `xoxp-`)

## Step 5: Configure slackcli

Add the token to your config file at `~/.config/slackcli/config.toml`:

```toml
default_org = "myworkspace"

[orgs.myworkspace]
token = "xoxp-your-token-here"
```

## Step 6: Test the Connection

```bash
# List your conversations
slack conversations list

# View messages in a channel
slack messages '#general' --today
```

## Token Types

| Token Prefix | Type | Description |
|--------------|------|-------------|
| `xoxp-` | User Token | Acts as the user, full visibility |
| `xoxb-` | Bot Token | Acts as a bot, limited to invited channels |
| `xapp-` | App Token | For Socket Mode connections only |

For slackcli, **User Tokens (`xoxp-`)** are recommended because they provide full visibility into all channels and DMs the user has access to.

## Security Notes

- Keep your token secret - it provides full access to your Slack account
- Store the token in the config file with restricted permissions (`chmod 600`)
- Never commit tokens to version control
- You can revoke the token at any time from the Slack app settings
