"""Which files of a library Stash would scan."""

import os
import re
from dataclasses import dataclass
from typing import NamedTuple


class Library(NamedTuple):
    path: str
    video: bool
    image: bool


@dataclass(frozen=True)
class Rules:
    """The extensions and exclusions that Stash applies to a scan."""

    video_extensions: frozenset[str]
    image_extensions: frozenset[str]
    video_excludes: tuple[re.Pattern[str], ...]
    image_excludes: tuple[re.Pattern[str], ...]

    @classmethod
    def build(
        cls,
        video_extensions: list[str],
        image_extensions: list[str],
        video_excludes: list[str],
        image_excludes: list[str],
    ) -> "Rules":
        def extensions(values: list[str]) -> frozenset[str]:
            return frozenset(value.lstrip(".").lower() for value in values)

        def patterns(values: list[str]) -> tuple[re.Pattern[str], ...]:
            return tuple(re.compile(value, re.IGNORECASE) for value in values)

        return cls(
            extensions(video_extensions),
            extensions(image_extensions),
            patterns(video_excludes),
            patterns(image_excludes),
        )

    def wants(self, library: Library, path: str) -> bool:
        extension = os.path.splitext(path)[1].lstrip(".").lower()

        def matches(extensions: frozenset[str], excludes: tuple[re.Pattern[str], ...]) -> bool:
            return extension in extensions and not any(pattern.search(path) for pattern in excludes)

        return (library.video and matches(self.video_extensions, self.video_excludes)) or (
            library.image and matches(self.image_extensions, self.image_excludes)
        )
