import base64
import time
from typing import Literal

from avif_codec import Encoder, compress_cover, resolve_executable
from stash_api import (
    Performer,
    download_image,
    find_performer,
    find_performers,
    image_size,
    update_performer_image,
)
from stash_plugin import FAILURES, Progress, StashClient, emit_debug, emit_info, emit_warn


def kib(size: int) -> str:
    return f"{size / 1024:.1f} KiB"


Outcome = tuple[Literal["compressed", "skipped"], str]


def compress_performer(
    client: StashClient, encoder: Encoder, performer: Performer, target_bytes: int
) -> Outcome:
    """Compresses one performer's cover if it is too big, and says what happened."""
    image_path = performer["image_path"]
    if not image_path:
        return "skipped", "no cover"
    reported_size = image_size(client, image_path)
    if reported_size is not None and reported_size <= target_bytes:
        return "skipped", f"already {kib(reported_size)}"
    original = download_image(client, image_path)
    if len(original) <= target_bytes:
        return "skipped", f"already {kib(len(original))}"

    start = time.perf_counter()
    cover = compress_cover(encoder, original, target_bytes)
    encoded = base64.b64encode(cover.data).decode("ascii")
    update_performer_image(client, performer["id"], f"data:image/avif;base64,{encoded}")
    return "compressed", (
        f"{kib(len(original))} -> {kib(len(cover.data))} at {cover.width}x{cover.height}, "
        f"CRF {cover.crf}, in {time.perf_counter() - start:.1f}s"
    )


def compress_covers(
    client: StashClient, ffmpeg_path: str | None, target_bytes: int, performer_id: str | None
) -> object:
    if performer_id is None:
        performers = find_performers(client)
    elif performer := find_performer(client, performer_id):
        performers = [performer]
    else:
        raise RuntimeError(f"Performer not found: {performer_id}")

    encoder = Encoder(resolve_executable(ffmpeg_path, "ffmpeg"))
    progress = Progress(len(performers))
    counts = {"compressed": 0, "skipped": 0, "failed": 0}
    for performer in performers:
        name = performer["name"]
        try:
            status, detail = compress_performer(client, encoder, performer, target_bytes)
        except FAILURES as exc:  # One broken cover must not stop the others.
            counts["failed"] += 1
            emit_warn(f"{name}: failed: {exc}")
        else:
            counts[status] += 1
            # Skipped covers would flood the log of a run over every performer.
            (emit_debug if status == "skipped" else emit_info)(f"{name}: {status}, {detail}")
        progress.step()

    emit_info(", ".join(f"{count} {status}" for status, count in counts.items()))
    return counts
