"""The arguments of `slack watch thread` and `slack watch channel`."""

from __future__ import annotations

import click
import pytest

from slackcli.watch.cli import ThreadRef, parse_channel_arg, parse_recheck_window, parse_thread_args

LINK = "https://example.slack.com/archives/C0987654321/p1700000900000200"
REPLY_LINK = (
    "https://example.slack.com/archives/C0987654321/p1700000950000300?thread_ts=1700000900.000200&cid=C0987654321"
)
CLIENT_LINK = "https://app.slack.com/client/T0123456789/C0987654321/thread/C0987654321-1700000900.000200"


def test_a_channel_applies_to_every_timestamp_after_it() -> None:
    assert parse_thread_args(["#general", "1700000000.123456", "1700000500.000100"]) == [
        ThreadRef("#general", "1700000000.123456"),
        ThreadRef("#general", "1700000500.000100"),
    ]


def test_a_link_carries_its_own_channel_and_leaves_the_current_one_alone() -> None:
    assert parse_thread_args(["#general", "1700000000.123456", LINK, "1700000500.000100"]) == [
        ThreadRef("#general", "1700000000.123456"),
        ThreadRef("C0987654321", "1700000900.000200", "example"),
        ThreadRef("#general", "1700000500.000100"),
    ]


def test_a_link_to_a_reply_names_its_thread() -> None:
    assert parse_thread_args([REPLY_LINK]) == [ThreadRef("C0987654321", "1700000900.000200", "example")]


def test_a_web_client_thread_link_names_no_workspace() -> None:
    assert parse_thread_args([CLIENT_LINK]) == [ThreadRef("C0987654321", "1700000900.000200", None)]


def test_several_channels_each_with_their_threads() -> None:
    refs = parse_thread_args(["C0123456789", "1700000000.000100", "#random", "1700000001.000100"])

    assert [(r.channel, r.ts) for r in refs] == [("C0123456789", "1700000000.000100"), ("#random", "1700000001.000100")]


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["1700000000.123456"], "name the channel before"),
        (["#general"], "followed by no thread timestamp"),
        (["#general", "#random", "1700000000.123456"], "followed by no thread timestamp"),
        (["#general", "yesterday"], "is not a channel, a thread timestamp"),
        (["https://example.slack.com/archives/C0987654321"], "not a link to a message"),
    ],
)
def test_arguments_that_name_no_thread_are_refused(args: list[str], message: str) -> None:
    with pytest.raises(click.UsageError, match=message):
        parse_thread_args(args)


def test_a_channel_argument_takes_a_name_an_id_or_a_link() -> None:
    assert parse_channel_arg("#general") == ("#general", None)
    assert parse_channel_arg("C0123456789") == ("C0123456789", None)
    assert parse_channel_arg("https://example.slack.com/archives/C0123456789") == ("C0123456789", "example")


def test_a_message_link_is_not_a_channel() -> None:
    with pytest.raises(click.UsageError, match="slack watch thread"):
        parse_channel_arg(LINK)


@pytest.mark.parametrize(("value", "seconds"), [("10m", 600.0), ("1h", 3600.0), ("0", 0.0)])
def test_the_recheck_window_is_a_duration(value: str, seconds: float) -> None:
    assert parse_recheck_window(value) == seconds
