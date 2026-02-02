"""Cache management for Slack CLI."""

import contextlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "slackcli"


@dataclass
class CacheMetadata:
    """Metadata about a cached file."""

    updated_at: str  # ISO format datetime
    version: int = 1


def get_cache_dir(org_name: str) -> Path:
    """Get the cache directory for an organization.

    Args:
        org_name: The organization name.

    Returns:
        Path to the organization's cache directory.
    """
    return DEFAULT_CACHE_DIR / org_name


def ensure_cache_dir(org_name: str) -> Path:
    """Ensure the cache directory exists for an organization.

    Args:
        org_name: The organization name.

    Returns:
        Path to the organization's cache directory.
    """
    cache_dir = get_cache_dir(org_name)
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_cache_path(org_name: str, cache_name: str) -> Path:
    """Get the path to a specific cache file.

    Args:
        org_name: The organization name.
        cache_name: The name of the cache (e.g., 'conversations').

    Returns:
        Path to the cache file.
    """
    return get_cache_dir(org_name) / f"{cache_name}.json"


def load_cache(org_name: str, cache_name: str) -> dict[str, Any] | None:
    """Load a cache file.

    Args:
        org_name: The organization name.
        cache_name: The name of the cache.

    Returns:
        The cached data, or None if not found or invalid.
    """
    cache_path = get_cache_path(org_name, cache_name)

    if not cache_path.exists():
        return None

    try:
        with open(cache_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        # Delete corrupted cache file to avoid re-reading it on every call
        with contextlib.suppress(OSError):
            cache_path.unlink(missing_ok=True)
        return None


def save_cache(org_name: str, cache_name: str, data: dict[str, Any]) -> Path:
    """Save data to a cache file.

    Args:
        org_name: The organization name.
        cache_name: The name of the cache.
        data: The data to cache.

    Returns:
        Path to the saved cache file.
    """
    ensure_cache_dir(org_name)
    cache_path = get_cache_path(org_name, cache_name)

    # Add metadata
    cache_data = {
        "metadata": asdict(
            CacheMetadata(
                updated_at=datetime.now().isoformat(),
            )
        ),
        "data": data,
    }

    with open(cache_path, "w") as f:
        json.dump(cache_data, f, indent=2)

    return cache_path


def get_cache_age(org_name: str, cache_name: str) -> datetime | None:
    """Get the age of a cache file.

    Args:
        org_name: The organization name.
        cache_name: The name of the cache.

    Returns:
        The datetime when the cache was last updated, or None if not found.
    """
    cache_data = load_cache(org_name, cache_name)

    if cache_data is None:
        return None

    metadata = cache_data.get("metadata", {})
    updated_at = metadata.get("updated_at")

    if updated_at:
        try:
            return datetime.fromisoformat(updated_at)
        except ValueError:
            return None

    return None
