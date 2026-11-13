"""Cache management for Slack CLI."""

import contextlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "slackcli"

# Schema version assumed for a cache file that records none; every writer so far has recorded 1
UNVERSIONED_CACHE_VERSION = 1


@dataclass
class CacheMetadata:
    """Metadata about a cached file."""

    updated_at: str  # ISO format datetime
    version: int


def is_current_version(recorded: object, current: int) -> bool:
    """Tell whether a cache entry was written by the current schema version.

    An entry written by another version is treated as expired: its shape may lack fields the current code
    expects, so it is fetched again rather than read.

    Args:
        recorded: The version stored with the entry, or None when it stores none.
        current: The schema version the current code writes.

    Returns:
        True when the entry was written by the current version.
    """
    if recorded is None:
        recorded = UNVERSIONED_CACHE_VERSION
    return recorded == current


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


def load_cache(org_name: str, cache_name: str, version: int) -> dict[str, Any] | None:
    """Load a cache file.

    Args:
        org_name: The organization name.
        cache_name: The name of the cache.
        version: The schema version the caller writes; a file written by another version counts as missing.

    Returns:
        The cached data, or None if not found, invalid or written by another schema version.
    """
    cache_path = get_cache_path(org_name, cache_name)

    if not cache_path.exists():
        return None

    try:
        with open(cache_path) as f:
            cache_data = json.load(f)
    except (json.JSONDecodeError, OSError):
        # Delete corrupted cache file to avoid re-reading it on every call
        with contextlib.suppress(OSError):
            cache_path.unlink(missing_ok=True)
        return None

    metadata = cache_data.get("metadata") if isinstance(cache_data, dict) else None
    recorded = metadata.get("version") if isinstance(metadata, dict) else None
    if not is_current_version(recorded, version):
        # Left in place: the next save overwrites it
        return None

    return cache_data


def save_cache(org_name: str, cache_name: str, data: dict[str, Any], version: int) -> Path:
    """Save data to a cache file.

    Args:
        org_name: The organization name.
        cache_name: The name of the cache.
        data: The data to cache.
        version: The schema version of the data, recorded in the metadata.

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
                version=version,
            )
        ),
        "data": data,
    }

    with open(cache_path, "w") as f:
        json.dump(cache_data, f, indent=2)

    return cache_path


def get_cache_age(org_name: str, cache_name: str, version: int) -> datetime | None:
    """Get the age of a cache file.

    Args:
        org_name: The organization name.
        cache_name: The name of the cache.
        version: The schema version the caller writes.

    Returns:
        The datetime when the cache was last updated, or None if not found or written by another schema version.
    """
    cache_data = load_cache(org_name, cache_name, version)

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
