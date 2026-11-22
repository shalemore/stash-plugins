"""Finding and removing the bytes a downloader put in front of a transport stream.

The stream itself is intact; only its first sync byte is somewhere after the start of the file,
so Stash probes the prefix (often a small PNG) and treats the file as an image. Locating the first
run of transport-stream packets and copying everything from there is lossless and leaves the
stream bytes untouched.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from stash_plugin import as_list, as_object, as_str, parse_json

TS_PACKET_SIZE = 188
SYNC_BYTE = 0x47
# Four consecutive packets are enough to tell a stream from a byte that happens to be 0x47.
SYNC_RUN = 4
# A prefix is an image or a handful of bytes; a stream that starts later is not our case.
PREFIX_SCAN_BYTES = 4 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
# A repaired file may lose a few frames; anything further off means the wrong stream was kept.
DURATION_TOLERANCE_SECONDS = 2.0
MIN_SIZE_RATIO = 0.5
MIN_DURATION_SECONDS = 5.0


@dataclass(frozen=True)
class MediaInfo:
    duration: float | None
    has_video: bool
    format_name: str


@dataclass(frozen=True)
class PrependedPrefix:
    """Where the transport stream starts, and what sits before it."""

    length: int
    signature: bytes


def find_prepended_prefix(path: Path) -> PrependedPrefix | None:
    """The prefix hiding a stream, or None when the file already starts with one."""
    header_bytes = TS_PACKET_SIZE * SYNC_RUN
    with path.open("rb") as handle:
        head = handle.read(header_bytes)
        if len(head) < header_bytes:
            return None
        if all(head[index * TS_PACKET_SIZE] == SYNC_BYTE for index in range(SYNC_RUN)):
            return None
        # Only a file that does not start with the stream needs the wider scan.
        _ = handle.seek(0)
        window = handle.read(PREFIX_SCAN_BYTES)
    last_start = len(window) - TS_PACKET_SIZE * (SYNC_RUN - 1)
    for offset in range(1, max(1, last_start)):
        if all(window[offset + index * TS_PACKET_SIZE] == SYNC_BYTE for index in range(SYNC_RUN)):
            return PrependedPrefix(length=offset, signature=window[:offset])
    return None


def probe_media(path: Path, *, force_ts: bool) -> MediaInfo | None:
    """Read duration and stream types; returns None when ffprobe cannot read the file."""
    command = ["ffprobe", "-v", "error", "-of", "json"]
    if force_ts:
        command.extend(["-f", "mpegts"])
    command.extend(["-show_entries", "format=duration,format_name:stream=codec_type", str(path)])
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        return None
    data = as_object(parse_json(result.stdout)) or {}
    fmt = as_object(data.get("format")) or {}
    duration_text = as_str(fmt.get("duration"))
    has_video = any(
        as_str((as_object(stream) or {}).get("codec_type")) == "video"
        for stream in as_list(data.get("streams"))
    )
    return MediaInfo(
        duration=float(duration_text) if duration_text else None,
        has_video=has_video,
        format_name=as_str(fmt.get("format_name")) or "",
    )


def copy_from(path: Path, target: Path, offset: int) -> None:
    with path.open("rb") as source, target.open("wb") as destination:
        _ = source.seek(offset)
        shutil.copyfileobj(source, destination, COPY_CHUNK_BYTES)


def is_acceptable(original: MediaInfo | None, repaired: MediaInfo, size_ratio: float) -> bool:
    """Whether the copy kept the stream, judged against what the original should contain."""
    if not repaired.has_video:
        return False
    if repaired.duration is None or repaired.duration < MIN_DURATION_SECONDS:
        return False
    if size_ratio < MIN_SIZE_RATIO:
        return False
    if original is not None and original.duration is not None:
        return repaired.duration >= original.duration - DURATION_TOLERANCE_SECONDS
    return True
