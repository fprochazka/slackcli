"""Tests for time parsing utilities."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from slackcli.time_utils import (
    parse_date_spec,
    parse_future_time,
    parse_relative_time,
    parse_time_spec,
)


class TestParseRelativeTime:
    """Tests for parse_relative_time()."""

    def test_parse_days(self) -> None:
        """Test parsing day specifications."""
        assert parse_relative_time("7d") == timedelta(days=7)
        assert parse_relative_time("1d") == timedelta(days=1)
        assert parse_relative_time("30d") == timedelta(days=30)
        assert parse_relative_time("365d") == timedelta(days=365)

    def test_parse_hours(self) -> None:
        """Test parsing hour specifications."""
        assert parse_relative_time("1h") == timedelta(hours=1)
        assert parse_relative_time("24h") == timedelta(hours=24)
        assert parse_relative_time("48h") == timedelta(hours=48)

    def test_parse_minutes(self) -> None:
        """Test parsing minute specifications."""
        assert parse_relative_time("30m") == timedelta(minutes=30)
        assert parse_relative_time("1m") == timedelta(minutes=1)
        assert parse_relative_time("60m") == timedelta(minutes=60)

    def test_parse_weeks(self) -> None:
        """Test parsing week specifications."""
        assert parse_relative_time("2w") == timedelta(weeks=2)
        assert parse_relative_time("1w") == timedelta(weeks=1)
        assert parse_relative_time("4w") == timedelta(weeks=4)

    def test_case_insensitive(self) -> None:
        """Test that parsing is case-insensitive."""
        assert parse_relative_time("7D") == timedelta(days=7)
        assert parse_relative_time("1H") == timedelta(hours=1)
        assert parse_relative_time("30M") == timedelta(minutes=30)
        assert parse_relative_time("2W") == timedelta(weeks=2)

    def test_whitespace_handling(self) -> None:
        """Test that leading/trailing whitespace is handled."""
        assert parse_relative_time("  7d  ") == timedelta(days=7)
        assert parse_relative_time("\t1h\n") == timedelta(hours=1)

    def test_invalid_inputs(self) -> None:
        """Test that invalid inputs return None."""
        assert parse_relative_time("") is None
        assert parse_relative_time("abc") is None
        assert parse_relative_time("7") is None
        assert parse_relative_time("d") is None
        assert parse_relative_time("7days") is None
        assert parse_relative_time("-7d") is None
        assert parse_relative_time("7.5d") is None
        assert parse_relative_time("7s") is None  # seconds not supported


class TestParseTimeSpec:
    """Tests for parse_time_spec()."""

    def test_today_keyword(self) -> None:
        """Test 'today' keyword returns start of today in UTC."""
        result = parse_time_spec("today")
        now = datetime.now(tz=timezone.utc)
        expected = now.replace(hour=0, minute=0, second=0, microsecond=0)
        assert result == expected

    def test_yesterday_keyword(self) -> None:
        """Test 'yesterday' keyword returns start of yesterday in UTC."""
        result = parse_time_spec("yesterday")
        now = datetime.now(tz=timezone.utc)
        expected = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        assert result == expected

    def test_now_keyword(self) -> None:
        """Test 'now' keyword returns current time in UTC."""
        before = datetime.now(tz=timezone.utc)
        result = parse_time_spec("now")
        after = datetime.now(tz=timezone.utc)
        assert before <= result <= after

    def test_relative_times(self) -> None:
        """Test relative time specifications."""
        before = datetime.now(tz=timezone.utc)
        result = parse_time_spec("7d")
        after = datetime.now(tz=timezone.utc)

        expected_min = before - timedelta(days=7)
        expected_max = after - timedelta(days=7)
        assert expected_min <= result <= expected_max

    def test_relative_hours(self) -> None:
        """Test relative hour specifications."""
        before = datetime.now(tz=timezone.utc)
        result = parse_time_spec("2h")
        after = datetime.now(tz=timezone.utc)

        expected_min = before - timedelta(hours=2)
        expected_max = after - timedelta(hours=2)
        assert expected_min <= result <= expected_max

    def test_iso_date(self) -> None:
        """Test ISO date parsing."""
        result = parse_time_spec("2024-01-15")
        expected = datetime(2024, 1, 15, 0, 0, 0, tzinfo=timezone.utc)
        assert result == expected

    def test_iso_datetime_with_t(self) -> None:
        """Test ISO datetime parsing with T separator."""
        result = parse_time_spec("2024-01-15T10:30:00")
        expected = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert result == expected

    def test_iso_datetime_with_space(self) -> None:
        """Test ISO datetime parsing with space separator."""
        result = parse_time_spec("2024-01-15 10:30:00")
        expected = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        assert result == expected

    def test_case_insensitive_keywords(self) -> None:
        """Test that keywords are case-insensitive."""
        result1 = parse_time_spec("TODAY")
        result2 = parse_time_spec("Today")
        result3 = parse_time_spec("today")
        # All should be the same (start of today)
        assert result1 == result2 == result3

    def test_invalid_spec_raises(self) -> None:
        """Test that invalid specifications raise ValueError."""
        with pytest.raises(ValueError, match="Cannot parse time specification"):
            parse_time_spec("invalid")

        with pytest.raises(ValueError, match="Cannot parse time specification"):
            parse_time_spec("not-a-date")

        with pytest.raises(ValueError, match="Cannot parse time specification"):
            parse_time_spec("")


class TestParseDateSpec:
    """Tests for parse_date_spec()."""

    def test_today_keyword(self) -> None:
        """Test 'today' keyword returns today's date."""
        result = parse_date_spec("today")
        now = datetime.now(tz=timezone.utc)
        assert result == now.strftime("%Y-%m-%d")

    def test_yesterday_keyword(self) -> None:
        """Test 'yesterday' keyword returns yesterday's date."""
        result = parse_date_spec("yesterday")
        now = datetime.now(tz=timezone.utc)
        expected = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        assert result == expected

    def test_relative_days(self) -> None:
        """Test relative day specifications."""
        result = parse_date_spec("7d")
        now = datetime.now(tz=timezone.utc)
        expected = (now - timedelta(days=7)).strftime("%Y-%m-%d")
        assert result == expected

    def test_relative_30_days(self) -> None:
        """Test 30 days ago."""
        result = parse_date_spec("30d")
        now = datetime.now(tz=timezone.utc)
        expected = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        assert result == expected

    def test_iso_date(self) -> None:
        """Test ISO date parsing returns same format."""
        result = parse_date_spec("2024-01-15")
        assert result == "2024-01-15"

    def test_iso_datetime_extracts_date(self) -> None:
        """Test ISO datetime parsing extracts just the date."""
        result = parse_date_spec("2024-01-15T10:30:00")
        assert result == "2024-01-15"

    def test_case_insensitive_keywords(self) -> None:
        """Test that keywords are case-insensitive."""
        result1 = parse_date_spec("TODAY")
        result2 = parse_date_spec("today")
        assert result1 == result2

    def test_invalid_spec_raises(self) -> None:
        """Test that invalid specifications raise ValueError."""
        with pytest.raises(ValueError, match="Cannot parse date specification"):
            parse_date_spec("invalid")

        with pytest.raises(ValueError, match="Cannot parse date specification"):
            parse_date_spec("7h")  # hours not supported for date spec

        with pytest.raises(ValueError, match="Cannot parse date specification"):
            parse_date_spec("")


class TestParseFutureTime:
    """Tests for parse_future_time()."""

    def test_relative_hours(self) -> None:
        """Test 'in Xh' format."""
        before = datetime.now().astimezone()
        result = parse_future_time("in 1h")
        after = datetime.now().astimezone()

        expected_min = before + timedelta(hours=1)
        expected_max = after + timedelta(hours=1)
        assert expected_min <= result <= expected_max

    def test_relative_minutes(self) -> None:
        """Test 'in Xm' format."""
        before = datetime.now().astimezone()
        result = parse_future_time("in 30m")
        after = datetime.now().astimezone()

        expected_min = before + timedelta(minutes=30)
        expected_max = after + timedelta(minutes=30)
        assert expected_min <= result <= expected_max

    def test_relative_days(self) -> None:
        """Test 'in Xd' format."""
        before = datetime.now().astimezone()
        result = parse_future_time("in 2d")
        after = datetime.now().astimezone()

        expected_min = before + timedelta(days=2)
        expected_max = after + timedelta(days=2)
        assert expected_min <= result <= expected_max

    def test_relative_case_insensitive(self) -> None:
        """Test relative time is case-insensitive."""
        result1 = parse_future_time("in 1H")
        result2 = parse_future_time("IN 1h")
        # Both should be approximately 1 hour from now
        now = datetime.now().astimezone()
        assert abs((result1 - now).total_seconds() - 3600) < 2
        assert abs((result2 - now).total_seconds() - 3600) < 2

    def test_tomorrow_default_9am(self) -> None:
        """Test 'tomorrow' defaults to 9am."""
        result = parse_future_time("tomorrow")
        tomorrow = (datetime.now().astimezone() + timedelta(days=1)).date()
        assert result.date() == tomorrow
        assert result.hour == 9
        assert result.minute == 0

    def test_tomorrow_with_time_12hour(self) -> None:
        """Test 'tomorrow Xam/pm' format."""
        result = parse_future_time("tomorrow 10am")
        tomorrow = (datetime.now().astimezone() + timedelta(days=1)).date()
        assert result.date() == tomorrow
        assert result.hour == 10
        assert result.minute == 0

    def test_tomorrow_with_pm(self) -> None:
        """Test 'tomorrow Xpm' format."""
        result = parse_future_time("tomorrow 3pm")
        tomorrow = (datetime.now().astimezone() + timedelta(days=1)).date()
        assert result.date() == tomorrow
        assert result.hour == 15
        assert result.minute == 0

    def test_tomorrow_12pm(self) -> None:
        """Test '12pm' is noon (not midnight)."""
        result = parse_future_time("tomorrow 12pm")
        assert result.hour == 12

    def test_tomorrow_12am(self) -> None:
        """Test '12am' is midnight."""
        result = parse_future_time("tomorrow 12am")
        assert result.hour == 0

    def test_tomorrow_with_24hour_time(self) -> None:
        """Test 'tomorrow 14:00' format."""
        result = parse_future_time("tomorrow 14:00")
        tomorrow = (datetime.now().astimezone() + timedelta(days=1)).date()
        assert result.date() == tomorrow
        assert result.hour == 14
        assert result.minute == 0

    def test_tomorrow_with_minutes(self) -> None:
        """Test 'tomorrow 9:30am' format."""
        result = parse_future_time("tomorrow 9:30am")
        tomorrow = (datetime.now().astimezone() + timedelta(days=1)).date()
        assert result.date() == tomorrow
        assert result.hour == 9
        assert result.minute == 30

    def test_iso_datetime_with_time(self) -> None:
        """Test ISO datetime parsing."""
        result = parse_future_time("2025-06-15 14:30")
        assert result.year == 2025
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 14
        assert result.minute == 30

    def test_iso_datetime_with_t_separator(self) -> None:
        """Test ISO datetime with T separator."""
        result = parse_future_time("2025-06-15T09:00:00")
        assert result.year == 2025
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 9
        assert result.minute == 0

    def test_iso_date_defaults_to_9am(self) -> None:
        """Test ISO date without time defaults to 9am."""
        result = parse_future_time("2025-06-15")
        assert result.year == 2025
        assert result.month == 6
        assert result.day == 15
        assert result.hour == 9
        assert result.minute == 0

    def test_invalid_spec_raises(self) -> None:
        """Test that invalid specifications raise ValueError."""
        with pytest.raises(ValueError, match="Cannot parse time specification"):
            parse_future_time("invalid")

        with pytest.raises(ValueError, match="Cannot parse time specification"):
            parse_future_time("next week")

        with pytest.raises(ValueError, match="Cannot parse time specification"):
            parse_future_time("")

    def test_invalid_tomorrow_time_raises(self) -> None:
        """Test that invalid time after 'tomorrow' raises ValueError."""
        with pytest.raises(ValueError, match="Cannot parse time in"):
            parse_future_time("tomorrow invalid")

        # "tomorrow 25:00" matches the time regex but fails on datetime.replace()
        # because 25 is not a valid hour
        with pytest.raises(ValueError, match="hour must be in 0..23"):
            parse_future_time("tomorrow 25:00")

    def test_whitespace_handling(self) -> None:
        """Test that leading/trailing whitespace is handled."""
        result = parse_future_time("  in 1h  ")
        now = datetime.now().astimezone()
        assert abs((result - now).total_seconds() - 3600) < 2
