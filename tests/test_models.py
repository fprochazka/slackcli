"""Tests for data models."""

from __future__ import annotations

from slackcli.models import format_file_size


class TestFormatFileSize:
    """Tests for format_file_size()."""

    def test_bytes(self) -> None:
        """Test formatting sizes in bytes."""
        assert format_file_size(0) == "0 B"
        assert format_file_size(1) == "1 B"
        assert format_file_size(512) == "512 B"
        assert format_file_size(1023) == "1023 B"

    def test_kilobytes(self) -> None:
        """Test formatting sizes in KB."""
        assert format_file_size(1024) == "1.0 KB"
        assert format_file_size(1536) == "1.5 KB"
        assert format_file_size(10 * 1024) == "10.0 KB"
        assert format_file_size(100 * 1024) == "100.0 KB"
        assert format_file_size(1024 * 1024 - 1) == "1024.0 KB"

    def test_megabytes(self) -> None:
        """Test formatting sizes in MB."""
        assert format_file_size(1024 * 1024) == "1.0 MB"
        assert format_file_size(int(1.5 * 1024 * 1024)) == "1.5 MB"
        assert format_file_size(10 * 1024 * 1024) == "10.0 MB"
        assert format_file_size(100 * 1024 * 1024) == "100.0 MB"
        assert format_file_size(1024 * 1024 * 1024 - 1) == "1024.0 MB"

    def test_gigabytes(self) -> None:
        """Test formatting sizes in GB."""
        assert format_file_size(1024 * 1024 * 1024) == "1.0 GB"
        assert format_file_size(int(1.5 * 1024 * 1024 * 1024)) == "1.5 GB"
        assert format_file_size(10 * 1024 * 1024 * 1024) == "10.0 GB"

    def test_edge_cases(self) -> None:
        """Test edge cases at unit boundaries."""
        # Just below 1 KB
        assert format_file_size(1023) == "1023 B"
        # Exactly 1 KB
        assert format_file_size(1024) == "1.0 KB"

        # Just below 1 MB
        assert format_file_size(1024 * 1024 - 1) == "1024.0 KB"
        # Exactly 1 MB
        assert format_file_size(1024 * 1024) == "1.0 MB"

        # Just below 1 GB
        assert format_file_size(1024 * 1024 * 1024 - 1) == "1024.0 MB"
        # Exactly 1 GB
        assert format_file_size(1024 * 1024 * 1024) == "1.0 GB"

    def test_decimal_precision(self) -> None:
        """Test that decimal precision is one digit."""
        # 1.23 KB should be shown as 1.2 KB
        assert format_file_size(1260) == "1.2 KB"
        # 1.99 KB
        assert format_file_size(2037) == "2.0 KB"

    def test_large_values(self) -> None:
        """Test very large file sizes."""
        # 100 GB
        assert format_file_size(100 * 1024 * 1024 * 1024) == "100.0 GB"
        # 1 TB (represented as 1024 GB)
        assert format_file_size(1024 * 1024 * 1024 * 1024) == "1024.0 GB"
