"""Batches file events, so that a burst of new files becomes one scan."""

import os
from collections import defaultdict
from collections.abc import Iterable
from itertools import chain


class Debouncer:
    """Collects paths until events stop for `quiet` seconds, or until `max_wait` seconds after the
    first one, so a steady stream of files cannot postpone the scan forever."""

    def __init__(self, quiet: float, max_wait: float) -> None:
        self.quiet: float = quiet
        self.max_wait: float = max_wait
        self._paths: set[str] = set()
        self._first: float = 0
        self._last: float = 0

    def add(self, path: str, now: float) -> None:
        if not self._paths:
            self._first = now
        self._paths.add(path)
        self._last = now

    def is_due(self, now: float) -> bool:
        return bool(self._paths) and (
            now - self._last >= self.quiet or now - self._first >= self.max_wait
        )

    def take(self) -> list[str]:
        paths = sorted(self._paths)
        self._paths.clear()
        return paths


def collapse(paths: Iterable[str], limit: int) -> list[str]:
    """Replaces the files of a directory by the directory itself when there are more than `limit`
    of them, so that a huge batch does not become a huge list of paths. Stash skips the files of a
    scanned directory that it already knows."""
    by_directory: defaultdict[str, list[str]] = defaultdict(list)
    for path in paths:
        by_directory[os.path.dirname(path)].append(path)

    crowded = {directory for directory, files in by_directory.items() if len(files) > limit}
    # A crowded directory inside another one is covered by the outer scan, and so are its files.
    inside = tuple(os.path.join(directory, "") for directory in crowded)
    roots = [directory for directory in crowded if not directory.startswith(inside)]
    files = chain.from_iterable(by_directory.values())
    return sorted([*roots, *(path for path in files if not path.startswith(inside))])
