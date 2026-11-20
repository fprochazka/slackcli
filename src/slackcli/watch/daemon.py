"""What `python -m slackcli.watch.daemon` runs: the shared daemon of `slack watch`. Watchers start it."""

from cli_watch_kit.daemon import main

from slackcli.watch.app import APP

if __name__ == "__main__":
    main(APP)
