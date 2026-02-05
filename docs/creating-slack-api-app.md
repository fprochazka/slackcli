# Creating a Slack API App

This guide walks you through creating a Slack app to get a User OAuth Token (`xoxp-`) for use with slackcli.

## Quick Setup: Using App Manifest

The fastest way to create a Slack app is using an App Manifest. This pre-configures all the necessary OAuth scopes automatically.

### Step 1: Choose Your Manifest

Choose either the **Read-Only** manifest (for viewing messages only) or the **Full Access** manifest (for reading and writing).

<details>
<summary><strong>Read-Only Manifest</strong> (click to expand)</summary>

```json
{
  "_metadata": {
    "major_version": 2,
    "minor_version": 1
  },
  "display_information": {
    "name": "Slack CLI",
    "description": "Command-line interface for Slack (read-only)",
    "long_description": "A CLI tool for reading Slack messages, channels, and conversations. This app provides read-only access to your Slack workspace.",
    "background_color": "#4A154B"
  },
  "features": {},
  "oauth_config": {
    "scopes": {
      "user": [
        "channels:history",
        "channels:read",
        "groups:history",
        "groups:read",
        "im:history",
        "im:read",
        "mpim:history",
        "mpim:read",
        "reactions:read",
        "search:read",
        "users:read"
      ]
    }
  },
  "settings": {
    "org_deploy_enabled": false,
    "socket_mode_enabled": false,
    "token_rotation_enabled": false
  }
}
```

</details>

<details>
<summary><strong>Full Access Manifest</strong> (click to expand)</summary>

```json
{
  "_metadata": {
    "major_version": 2,
    "minor_version": 1
  },
  "display_information": {
    "name": "Slack CLI",
    "description": "Command-line interface for Slack (full access)",
    "long_description": "A CLI tool for interacting with Slack. This app provides full read and write access to messages, channels, files, and more in your Slack workspace. Enables automation, scripting, and programmatic control of Slack operations directly from the terminal.",
    "background_color": "#4A154B"
  },
  "features": {},
  "oauth_config": {
    "scopes": {
      "user": [
        "channels:history",
        "channels:read",
        "channels:write",
        "chat:write",
        "dnd:read",
        "dnd:write",
        "files:read",
        "files:write",
        "groups:history",
        "groups:read",
        "groups:write",
        "im:history",
        "im:read",
        "im:write",
        "links:read",
        "links:write",
        "mpim:history",
        "mpim:read",
        "mpim:write",
        "pins:read",
        "pins:write",
        "reactions:read",
        "reactions:write",
        "reminders:read",
        "reminders:write",
        "search:read",
        "stars:read",
        "stars:write",
        "team:read",
        "usergroups:read",
        "usergroups:write",
        "users.profile:read",
        "users:read",
        "users:read.email"
      ]
    }
  },
  "settings": {
    "org_deploy_enabled": false,
    "socket_mode_enabled": false,
    "token_rotation_enabled": false
  }
}
```

</details>

### Step 2: Create the App from Manifest

1. Go to [https://api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App**
3. Select **From an app manifest**
4. Select your **Workspace** and click **Next**
5. Choose **JSON** as the format (should be selected by default)
6. Delete the example manifest and paste your chosen manifest from above
7. Click **Next** to review the configuration
8. Click **Create** to create the app

### Step 3: Install and Get Your Token

1. After creation, you will be on the **Basic Information** page
2. Navigate to **Install App** in the left sidebar
3. Click **Install to Workspace**
4. Review the permissions and click **Allow**
5. Copy the **User OAuth Token** (starts with `xoxp-`)

### Step 4: Configure slackcli

Add the token to your config file at `~/.config/slackcli/config.toml`:

```toml
default_org = "myworkspace"

[orgs.myworkspace]
token = "xoxp-your-token-here"
```

### Step 5: Test the Connection

```bash
# List your conversations
slack conversations list

# View messages in a channel
slack messages '#general' --today
```

---

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
