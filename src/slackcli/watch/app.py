"""The `WatchApp` of `slack watch`. Importing this module builds no source and imports no Slack client."""

from __future__ import annotations

from cli_watch_kit.app import WatchApp
from cli_watch_kit.source import Source

from slackcli import __version__


def make_source() -> Source[object]:
    from slackcli.watch.source import SlackSource

    return SlackSource()  # type: ignore[return-value]


APP = WatchApp(
    name="slack-watch",
    source=make_source,
    daemon_module="slackcli.watch.daemon",
    app_protocol=1,
    version=__version__,
    status_prefix="slack watch: ",
    envelope_tag="slack-watcher",
    command="slack watch",
)
