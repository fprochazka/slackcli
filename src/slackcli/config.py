"""Configuration management for Slack CLI."""

from dataclasses import dataclass, field
from pathlib import Path

import tomli

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "slackcli" / "config.toml"


@dataclass
class OrgConfig:
    """Configuration for a single Slack organization."""

    name: str
    token: str


@dataclass
class Config:
    """Main configuration class."""

    orgs: dict[str, OrgConfig] = field(default_factory=dict)
    default_org: str | None = None

    def get_org(self, name: str | None = None) -> OrgConfig:
        """Get organization config by name, or default if not specified.

        Args:
            name: Organization name, or None to use default.

        Returns:
            The organization configuration.

        Raises:
            ValueError: If org not found or no default configured.
        """
        if name is None:
            name = self.default_org

        if name is None:
            if len(self.orgs) == 1:
                # If only one org, use it as default
                return next(iter(self.orgs.values()))
            raise ValueError(
                "No organization specified and no default configured. Use --org=<name> or set default_org in config."
            )

        if name not in self.orgs:
            available = ", ".join(self.orgs.keys()) if self.orgs else "(none)"
            raise ValueError(f"Organization '{name}' not found. Available: {available}")

        return self.orgs[name]


def load_config(config_path: Path | None = None) -> Config:
    """Load configuration from TOML file.

    Args:
        config_path: Path to config file, or None for default.

    Returns:
        Parsed configuration.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValueError: If config is invalid.
    """
    path = config_path or DEFAULT_CONFIG_PATH

    if not path.exists():
        raise FileNotFoundError(
            f"Config file not found: {path}\n"
            f"Create it with your Slack token(s). Example:\n\n"
            f"[orgs.myworkspace]\n"
            f'token = "xoxp-..."'
        )

    with open(path, "rb") as f:
        data = tomli.load(f)

    config = Config()
    config.default_org = data.get("default_org")

    orgs_data = data.get("orgs", {})
    for org_name, org_data in orgs_data.items():
        if not isinstance(org_data, dict):
            raise ValueError(f"Invalid org config for '{org_name}': expected table")

        token = org_data.get("token")
        if not token:
            raise ValueError(f"Missing 'token' for org '{org_name}'")

        config.orgs[org_name] = OrgConfig(
            name=org_name,
            token=token,
        )

    return config


def get_config_path() -> Path:
    """Get the default config file path."""
    return DEFAULT_CONFIG_PATH
