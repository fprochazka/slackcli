"""Configuration management for Slack CLI."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import tomli

from .signature import SIGNATURE_MODES

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "slackcli" / "config.toml"

# Overrides the top-level 'agent_signature' key of the config file.
AGENT_SIGNATURE_ENV_VAR = "SLACK_AGENT_SIGNATURE"


@dataclass
class OrgConfig:
    """Configuration for a single Slack organization."""

    name: str
    token: str
    workspaces: list[str] = field(default_factory=list)


@dataclass
class Config:
    """Main configuration class."""

    orgs: dict[str, OrgConfig] = field(default_factory=dict)
    default_org: str | None = None
    # Workspace name -> org key.
    workspace_index: dict[str, str] = field(default_factory=dict)
    # How messages sent from an AI agent are signed: off, plain or marketing.
    agent_signature: str = "marketing"

    def describe_orgs(self) -> str:
        """Describe the configured orgs and their workspace names.

        Returns:
            A comma separated list for error messages.
        """
        if not self.orgs:
            return "(none)"

        parts = []
        for org in self.orgs.values():
            if org.workspaces:
                parts.append(f"{org.name} (workspaces: {', '.join(org.workspaces)})")
            else:
                parts.append(org.name)
        return ", ".join(parts)

    def get_org(self, name: str | None = None) -> OrgConfig:
        """Get organization config by name, or default if not specified.

        The name matches an org key first, then a workspace name. An org key
        therefore never gets shadowed by a workspace name.

        Args:
            name: Organization name, workspace name, or None to use default.

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

        if name in self.orgs:
            return self.orgs[name]

        org_name = self.workspace_index.get(name)
        if org_name is not None:
            return self.orgs[org_name]

        raise ValueError(f"Organization '{name}' not found. Available: {self.describe_orgs()}")


def _parse_workspaces(org_name: str, org_data: dict) -> list[str]:
    """Read the optional 'workspaces' key of one org.

    Args:
        org_name: The org key, for error messages.
        org_data: The org table.

    Returns:
        The workspace names, empty if the key is absent.

    Raises:
        ValueError: If the value is not a list of strings.
    """
    workspaces = org_data.get("workspaces", [])
    if not isinstance(workspaces, list) or not all(isinstance(item, str) for item in workspaces):
        raise ValueError(f"Invalid 'workspaces' for org '{org_name}': expected a list of strings")
    return workspaces


def _build_workspace_index(orgs: dict[str, OrgConfig]) -> dict[str, str]:
    """Map every workspace name to its org key.

    Args:
        orgs: The loaded orgs.

    Returns:
        Workspace name -> org key.

    Raises:
        ValueError: If a workspace name repeats or collides with an org key.
    """
    index: dict[str, str] = {}
    for org_name, org in orgs.items():
        for workspace in org.workspaces:
            if workspace == org_name:
                raise ValueError(f"Workspace '{workspace}' of org '{org_name}' repeats the org name. Remove it.")
            if workspace in orgs:
                raise ValueError(
                    f"Workspace '{workspace}' of org '{org_name}' collides with the org named '{workspace}'."
                )
            if workspace in index:
                owner = index[workspace]
                if owner == org_name:
                    raise ValueError(f"Workspace '{workspace}' is listed twice for org '{org_name}'.")
                raise ValueError(f"Workspace '{workspace}' belongs to org '{owner}' and to org '{org_name}'.")
            index[workspace] = org_name
    return index


def _validate_signature_mode(value: object, source: str) -> str:
    """Normalise and check one agent signature mode.

    Args:
        value: The raw value, from the environment or from the config file.
        source: How the value is named in the error message.

    Returns:
        The mode, lower-cased and trimmed when it is a string.

    Raises:
        ValueError: If the value is not one of the known modes.
    """
    mode = value.strip().lower() if isinstance(value, str) else value
    if mode not in SIGNATURE_MODES:
        raise ValueError(f"Invalid {source}: expected one of {', '.join(SIGNATURE_MODES)}")
    return str(mode)


def _parse_agent_signature(data: dict) -> str:
    """Read the agent signature mode from the environment, then from the config file.

    SLACK_AGENT_SIGNATURE wins over the top-level 'agent_signature' key. A variable that
    is unset, empty or whitespace-only counts as absent, so exporting an empty value
    leaves the config file in charge.

    Args:
        data: The parsed config file.

    Returns:
        The mode, lower-cased and trimmed, or the default when neither source sets one.

    Raises:
        ValueError: If either value is not one of the known modes.
    """
    from_env = os.environ.get(AGENT_SIGNATURE_ENV_VAR)
    if from_env is not None and from_env.strip():
        return _validate_signature_mode(from_env, AGENT_SIGNATURE_ENV_VAR)

    return _validate_signature_mode(data.get("agent_signature", "marketing"), "'agent_signature'")


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
    config.agent_signature = _parse_agent_signature(data)

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
            workspaces=_parse_workspaces(org_name, org_data),
        )

    config.workspace_index = _build_workspace_index(config.orgs)

    return config


def get_config_path() -> Path:
    """Get the default config file path."""
    return DEFAULT_CONFIG_PATH
