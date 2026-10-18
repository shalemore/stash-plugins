"""The watcher: scans the new files of the libraries as they appear.

Stash runs a plugin as a short process, so a task starts this script as a detached process (see
daemon.py). Only libraries on a local disk of Linux or Windows are watched (see notify.py).
"""

import logging
import os
import queue
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from daemon import STOP, lock, state_directory, take_request
from debounce import Debouncer, collapse
from libraries import Library, Rules
from notify import Backend, Event, Events, filesystem, open_backend
from stash_api import WatchConfig, get_watch_config, scan_is_busy, scan_paths
from stash_plugin import FAILURES, StashClient, read_plugin_input

log = logging.getLogger("watch-libraries")

TICK_SECONDS = 1
CONFIG_SECONDS = 60
RETRY_SECONDS = 30
# More new files than this in one directory are scanned as the directory.
COLLAPSE_LIMIT = 200


def number_setting(settings: dict[str, object], key: str, default: float) -> float:
    value = settings.get(key)
    try:
        return max(float(value), 0) if isinstance(value, int | float | str) else default
    except ValueError:
        return default


class Watcher:
    def __init__(self, client: StashClient, state_dir: Path) -> None:
        self.client: StashClient = client
        self.state_dir: Path = state_dir
        self.events: Events = queue.SimpleQueue()
        self.backend: Backend | None = open_backend(self.events)
        self.debouncer: Debouncer = Debouncer(10, 120)
        self.scan_options: dict[str, bool | None] = {}
        self.rules: Rules | None = None
        self.libraries: dict[str, Library] = {}
        self.watched: set[str] = set()

    def run(self) -> None:
        log.info("Started")
        next_config = retry_at = 0.0
        while not take_request(self.state_dir, STOP):
            now = time.time()
            if now >= retry_at:
                try:
                    if now >= next_config:
                        self.configure(get_watch_config(self.client))
                        next_config = now + CONFIG_SECONDS
                    self.flush(now)
                except FAILURES as exc:
                    log.warning("Failed, retrying in %s seconds: %s", RETRY_SECONDS, exc)
                    retry_at = now + RETRY_SECONDS
            self.receive(TICK_SECONDS)
        log.info("Stopped")

    def configure(self, config: WatchConfig) -> None:
        settings = config["settings"]
        self.debouncer.quiet = number_setting(settings, "quiet_seconds", 10)
        self.debouncer.max_wait = number_setting(settings, "max_wait_seconds", 120)
        self.scan_options = config["scan_options"]
        self.rules = Rules.build(
            config["video_extensions"],
            config["image_extensions"],
            config["video_excludes"],
            config["image_excludes"],
        )
        self.libraries = {
            item["path"]: Library(item["path"], not item["excludeVideo"], not item["excludeImage"])
            for item in config["libraries"]
            if not (item["excludeVideo"] and item["excludeImage"])
        }
        for path in self.libraries.keys() - self.watched:
            self.watched.add(path)
            self.watch(path)

    def watch(self, path: str) -> None:
        # A library that is removed later keeps its watch until the watcher restarts, but its
        # files no longer match a library.
        if not self.backend:
            log.warning("%s: not watched, this system reports no file events", path)
            return
        fstype, local = filesystem(path)
        if not local:
            log.warning("%s: not watched, %s may change without the system noticing", path, fstype)
        elif self.backend.watch(path):
            log.info("%s: watched (%s)", path, fstype)
        else:
            log.warning(
                "%s: only partly watched, the system limits how many folders it watches", path
            )

    def library_of(self, path: str) -> Library | None:
        inside = [p for p in self.libraries if path.startswith(os.path.join(p, ""))]
        return self.libraries[max(inside, key=len)] if inside else None

    def add_file(self, path: str, now: float) -> None:
        library = self.library_of(path)
        if library and self.rules and self.rules.wants(library, path):
            self.debouncer.add(path, now)

    def receive(self, timeout: float) -> None:
        """Waits up to `timeout` for events, then handles all that arrived."""
        try:
            event = self.events.get(timeout=timeout)
            while True:
                self.handle(event, time.time())
                event = self.events.get_nowait()
        except queue.Empty:
            pass

    def handle(self, event: Event, now: float) -> None:
        if event.overflow:
            log.warning("The system dropped file events; scan the libraries if files are missing")
        elif event.is_dir:
            # Files can land in a new directory before the system reports them.
            for directory, _, files in os.walk(event.path):
                for name in files:
                    self.add_file(os.path.join(directory, name), now)
        else:
            self.add_file(event.path, now)

    def flush(self, now: float) -> None:
        if not self.debouncer.is_due(now) or scan_is_busy(self.client):
            return
        ready: list[str] = []
        for path in self.debouncer.take():
            try:
                modified = os.stat(path).st_mtime
            except OSError:
                continue
            # A file that is still being written, or that a downloader has not finished, waits
            # for the next batch.
            if now - modified < self.debouncer.quiet or os.path.exists(f"{path}.aria2"):
                self.debouncer.add(path, now)
            else:
                ready.append(path)
        if ready:
            paths = collapse(ready, COLLAPSE_LIMIT)
            job = scan_paths(self.client, paths, self.scan_options)
            log.info("Scanning %d new files as %d paths (job %s)", len(ready), len(paths), job)


def main() -> None:
    plugin_input = read_plugin_input()
    state_dir = state_directory(plugin_input)
    handler = RotatingFileHandler(
        state_dir / "watcher.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8"
    )
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[handler]
    )
    # The lock stays held, and so marks the watcher as running, until the process ends.
    if not (held := lock(state_dir)):
        log.info("Another watcher is running")
        return
    with held:
        Watcher(StashClient.connect(plugin_input), state_dir).run()


if __name__ == "__main__":
    main()
