"""Time parsing utilities for Slack CLI.

This module provides shared time parsing functions used across commands
for handling relative times, date specifications, and future scheduling.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


def parse_relative_time(spec: str, base: datetime | None = None) -> timedelta | None:
    """Parse a relative time specification into a timedelta.

    Supports formats like:
    - "7d" (7 days)
    - "1h" (1 hour)
    - "30m" (30 minutes)
    - "2w" (2 weeks)

    Args:
        spec: The time specification string (e.g., "7d", "1h").
        base: Optional base datetime (unused, kept for potential future extension).

    Returns:
        A timedelta representing the relative time, or None if format doesn't match.
    """
    spec = spec.strip().lower()

    relative_match = re.match(r"^(\d+)([hdwm])$", spec)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2)
        if unit == "h":
            return timedelta(hours=amount)
        if unit == "d":
            return timedelta(days=amount)
        if unit == "w":
            return timedelta(weeks=amount)
        if unit == "m":
            return timedelta(minutes=amount)

    return None


def parse_iso_datetime(spec: str) -> datetime | None:
    """Parse an ISO date or datetime string.

    Supports:
    - ISO date: "2024-01-15"
    - ISO datetime: "2024-01-15T10:30:00" or "2024-01-15 10:30:00"

    Args:
        spec: The date/datetime string.

    Returns:
        Parsed datetime (timezone-naive), or None if format doesn't match.
    """
    spec = spec.strip()

    try:
        # Try datetime with time
        if "T" in spec or " " in spec:
            return datetime.fromisoformat(spec.replace(" ", "T"))
        else:
            # Just date, start of day
            dt = datetime.fromisoformat(spec)
            return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        return None


def parse_time_spec(spec: str) -> datetime:
    """Parse a time specification into a datetime for message filtering.

    All times are interpreted relative to UTC and returned as UTC-aware datetimes.

    Supports:
    - ISO date: "2024-01-15"
    - ISO datetime: "2024-01-15T10:30:00"
    - Relative: "7d", "1h", "2w", "30m"
    - Keywords: "today", "yesterday", "now"

    Args:
        spec: The time specification string.

    Returns:
        Parsed datetime in UTC.

    Raises:
        ValueError: If spec cannot be parsed.
    """
    spec_lower = spec.strip().lower()
    now = datetime.now(tz=timezone.utc)

    # Keywords
    if spec_lower == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if spec_lower == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    if spec_lower == "now":
        return now

    # Relative time: 7d, 1h, 2w, 30m
    delta = parse_relative_time(spec_lower)
    if delta is not None:
        return now - delta

    # ISO date/datetime
    dt = parse_iso_datetime(spec)
    if dt is not None:
        # If no timezone, assume UTC
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt

    raise ValueError(f"Cannot parse time specification: {spec}")


def parse_date_spec(spec: str) -> str:
    """Parse a date specification into YYYY-MM-DD format for Slack search.

    Supports:
    - ISO date: "2024-01-15"
    - Relative: "7d", "30d"
    - Keywords: "today", "yesterday"

    Args:
        spec: The date specification string.

    Returns:
        Date string in YYYY-MM-DD format.

    Raises:
        ValueError: If spec cannot be parsed.
    """
    spec_lower = spec.strip().lower()
    now = datetime.now(tz=timezone.utc)

    # Keywords
    if spec_lower == "today":
        return now.strftime("%Y-%m-%d")
    if spec_lower == "yesterday":
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # Relative time: 7d, 30d (only days supported for search)
    relative_match = re.match(r"^(\d+)d$", spec_lower)
    if relative_match:
        days = int(relative_match.group(1))
        return (now - timedelta(days=days)).strftime("%Y-%m-%d")

    # ISO date: 2024-01-15
    dt = parse_iso_datetime(spec)
    if dt is not None:
        return dt.strftime("%Y-%m-%d")

    raise ValueError(f"Cannot parse date specification: {spec}")


def parse_future_time(spec: str) -> datetime:
    """Parse a future time specification into a datetime.

    All times are interpreted in the user's local timezone. The returned
    datetime is timezone-aware (local timezone) and can be converted to
    a Unix timestamp for the Slack API.

    Supports:
    - ISO datetime: "2024-01-15 09:00", "2024-01-15T09:00"
    - Relative future: "in 1h", "in 30m", "in 2d"
    - Natural language: "tomorrow", "tomorrow 9am", "tomorrow 14:00"

    Args:
        spec: The time specification string.

    Returns:
        Parsed datetime in local timezone (always in the future).

    Raises:
        ValueError: If spec cannot be parsed.

    Note:
        This function does NOT validate that the time is in the future.
        The caller should perform that validation.
    """
    spec = spec.strip()
    # Use local time for all parsing - users expect "9am" to mean 9am local time
    now = datetime.now().astimezone()
    local_tz = now.tzinfo

    # Relative future time: "in 1h", "in 30m", "in 2d"
    relative_match = re.match(r"^in\s+(\d+)\s*([hdm])$", spec, re.IGNORECASE)
    if relative_match:
        amount = int(relative_match.group(1))
        unit = relative_match.group(2).lower()
        if unit == "h":
            return now + timedelta(hours=amount)
        if unit == "d":
            return now + timedelta(days=amount)
        if unit == "m":
            return now + timedelta(minutes=amount)

    # "tomorrow" or "tomorrow 9am" or "tomorrow 14:00"
    if spec.lower().startswith("tomorrow"):
        tomorrow = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        rest = spec[8:].strip()  # After "tomorrow"
        if rest:
            # Try to parse time part: "9am", "14:00", "9:30am", "9:30"
            time_match = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", rest, re.IGNORECASE)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2)) if time_match.group(2) else 0
                ampm = time_match.group(3)

                if ampm:
                    ampm = ampm.lower()
                    if ampm == "pm" and hour != 12:
                        hour += 12
                    elif ampm == "am" and hour == 12:
                        hour = 0

                tomorrow = tomorrow.replace(hour=hour, minute=minute)
            else:
                raise ValueError(f"Cannot parse time in: {spec}")
        return tomorrow

    # ISO datetime: "2024-01-15 09:00" or "2024-01-15T09:00:00"
    try:
        # Try datetime with time
        if "T" in spec or " " in spec:
            # Normalize separator
            dt = datetime.fromisoformat(spec.replace(" ", "T"))
        else:
            # Just date, default to 9am
            dt = datetime.fromisoformat(spec)
            dt = dt.replace(hour=9, minute=0, second=0, microsecond=0)

        # If no timezone specified, assume local timezone
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=local_tz)

        return dt
    except ValueError:
        pass

    raise ValueError(f"Cannot parse time specification: {spec}")
