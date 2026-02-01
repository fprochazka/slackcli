"""CLI context management for Slack CLI."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config, OrgConfig


@dataclass
class Context:
    """CLI context passed to all commands."""

    config: "Config | None" = None
    org_name: str | None = None
    verbose: bool = False

    def get_org(self) -> "OrgConfig":
        """Get the selected organization config."""
        from .config import load_config

        if self.config is None:
            self.config = load_config()
        return self.config.get_org(self.org_name)

    def get_token(self) -> str:
        """Get the token for the selected organization."""
        return self.get_org().token


# Global context instance
_ctx = Context()


def get_context() -> Context:
    """Get the current CLI context."""
    return _ctx
