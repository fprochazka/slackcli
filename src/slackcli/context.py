"""CLI context management for Slack CLI."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import SlackCli
    from .config import Config, OrgConfig


@dataclass
class Context:
    """CLI context passed to all commands."""

    config: "Config | None" = None
    config_path: Path | None = None
    org_name: str | None = None
    verbose: bool = False

    def get_config(self) -> "Config":
        """Get the configuration, loading it on first use.

        Returns:
            The parsed configuration.
        """
        from .config import load_config

        if self.config is None:
            self.config = load_config(self.config_path)
        return self.config

    def get_org(self) -> "OrgConfig":
        """Get the selected organization config."""
        return self.get_config().get_org(self.org_name)

    def get_token(self) -> str:
        """Get the token for the selected organization."""
        return self.get_org().token

    def get_slack_client(self) -> "SlackCli":
        """Create a SlackCli instance from the context.

        Returns:
            SlackCli instance configured with org name and token.
        """
        from .client import SlackCli

        org = self.get_org()
        return SlackCli(org_name=org.name, token=org.token)


# Global context instance
_ctx = Context()


def get_context() -> Context:
    """Get the current CLI context."""
    return _ctx
