import base64
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Literal, TypedDict

from avif_codec import MAX_QUALITY, compress_cover, resolve_executable, validate_ffmpeg
from stash_api import (
    Performer,
    download_image,
    find_performer,
    find_performers,
    get_runtime_config,
    image_size,
    update_performer_image,
)
from stash_plugin import (
    PluginInput,
    StashClient,
    as_bool,
    emit_info,
    emit_progress,
    emit_warn,
    run_plugin,
)

DEFAULT_TARGET_KB = 100
DEFAULT_MAX_WIDTH = 720
DEFAULT_MIN_QUALITY = 60
DEFAULT_CONCURRENCY = 3

Status = Literal["updated", "preview", "skipped", "failed"]


@dataclass(frozen=True)
class Options:
    apply: bool
    target_bytes: int
    max_width: int
    min_quality: int
    concurrency: int
    limit: int
    ffmpeg_path: str | None


class Compressed(TypedDict):
    status: Literal["updated", "preview"]
    original_bytes: int
    compressed_bytes: int
    quality: int
    width: int
    height: int


class Skipped(TypedDict):
    status: Literal["skipped"]
    reason: str


def parse_int(
    settings: dict[str, object],
    key: str,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw = settings.get(key)
    if raw is None or raw == "":
        return default

    try:
        value = int(raw) if isinstance(raw, int | float | str) else int(str(raw))
    except ValueError as exc:
        raise ValueError(f"Setting {key} must be an integer; received: {raw}") from exc

    if value < minimum or (maximum is not None and value > maximum):
        allowed = (
            f"between {minimum} and {maximum}" if maximum is not None else f"at least {minimum}"
        )
        raise ValueError(f"Setting {key} must be {allowed}; received: {value}")
    return value


def load_options(args: dict[str, object], settings: dict[str, object]) -> Options:
    target_kb = parse_int(settings, "target_kb", DEFAULT_TARGET_KB, 1)
    ffmpeg_path = str(settings.get("ffmpeg_path") or "").strip() or None
    return Options(
        apply=as_bool(args.get("apply"), False),
        target_bytes=target_kb * 1024,
        max_width=parse_int(settings, "max_width", DEFAULT_MAX_WIDTH, 160),
        min_quality=parse_int(settings, "min_quality", DEFAULT_MIN_QUALITY, 1, MAX_QUALITY),
        concurrency=parse_int(settings, "concurrency", DEFAULT_CONCURRENCY, 1, 16),
        limit=parse_int(settings, "limit", 0, 0),
        ffmpeg_path=ffmpeg_path,
    )


def format_bytes(size: int) -> str:
    return f"{size / 1024:.1f} KiB"


def select_performers(
    client: StashClient, plugin_input: PluginInput, limit: int
) -> tuple[list[Performer], bool]:
    hook = plugin_input.hook
    if not hook:
        performers = find_performers(client)
        return (performers[:limit] if limit else performers), False

    if "image" not in hook.input_fields:
        emit_info("The performer update did not include the image field; skipping compression")
        return [], True
    if hook.id is None:
        raise RuntimeError("Performer.Update.Post hook did not provide a performer ID")

    performer = find_performer(client, hook.id)
    if not performer:
        raise RuntimeError(f"Performer not found: {hook.id}")
    return [performer], True


def process_performer(
    client: StashClient,
    performer: Performer,
    options: Options,
    ffmpeg_path: str,
    ffprobe_path: str,
) -> Compressed | Skipped:
    image_path = performer["image_path"]
    if not image_path:
        return {"status": "skipped", "reason": "No cover"}

    remote_size = image_size(client, image_path)
    if remote_size is not None and remote_size <= options.target_bytes:
        return {
            "status": "skipped",
            "reason": f"Already below threshold at {format_bytes(remote_size)}",
        }

    original = download_image(client, image_path)
    if len(original) <= options.target_bytes:
        return {
            "status": "skipped",
            "reason": f"Already below threshold at {format_bytes(len(original))}",
        }

    compressed = compress_cover(
        source=original,
        ffmpeg_path=ffmpeg_path,
        ffprobe_path=ffprobe_path,
        target_bytes=options.target_bytes,
        min_quality=options.min_quality,
        max_width=options.max_width,
    )
    if options.apply:
        encoded = base64.b64encode(compressed.data).decode("ascii")
        update_performer_image(client, performer["id"], f"data:image/avif;base64,{encoded}")

    return {
        "status": "updated" if options.apply else "preview",
        "original_bytes": len(original),
        "compressed_bytes": len(compressed.data),
        "quality": compressed.quality,
        "width": compressed.width,
        "height": compressed.height,
    }


def describe(name: str, result: Compressed | Skipped) -> str:
    if result["status"] == "skipped":
        return f"Skipped {name}: {result['reason']}"
    action = "Updated" if result["status"] == "updated" else "Compressible"
    return (
        f"{action} {name}: {format_bytes(result['original_bytes'])} -> "
        f"{format_bytes(result['compressed_bytes'])}, quality={result['quality']}, "
        f"{result['width']}x{result['height']}"
    )


def run(plugin_input: PluginInput) -> object:
    client = StashClient.connect(plugin_input)
    system, settings = get_runtime_config(client)
    options = load_options(plugin_input.args, settings)
    summary: dict[Status, int] = {"updated": 0, "preview": 0, "skipped": 0, "failed": 0}
    performers, is_hook = select_performers(client, plugin_input, options.limit)
    if not performers:
        return {"apply": options.apply, "summary": summary, "results": []}

    ffmpeg_path = resolve_executable(options.ffmpeg_path or system["ffmpegPath"], "ffmpeg")
    ffprobe_path = resolve_executable(system["ffprobePath"], "ffprobe")
    validate_ffmpeg(ffmpeg_path)

    mode = "write to Stash" if options.apply else "preview without writing"
    source = "Performer.Update.Post hook" if is_hook else "manual task"
    emit_info(
        f"Processing {len(performers)} performers; source={source}; mode={mode}; "
        f"target={format_bytes(options.target_bytes)}; max_width={options.max_width}; "
        f"min_quality={options.min_quality}; concurrency={options.concurrency}"
    )
    emit_info(f"FFmpeg={ffmpeg_path}")
    emit_progress(0.0)

    results: list[dict[str, object]] = []
    progress_lock = threading.Lock()
    completed = 0

    with ThreadPoolExecutor(max_workers=options.concurrency) as executor:
        futures = {
            executor.submit(
                process_performer, client, performer, options, ffmpeg_path, ffprobe_path
            ): performer
            for performer in performers
        }
        for future in as_completed(futures):
            performer = futures[future]
            identity = {"id": performer["id"], "name": performer["name"]}
            try:
                result = future.result()
                summary[result["status"]] += 1
                results.append({**identity, **result})
                emit_info(describe(performer["name"], result))
            except Exception as exc:  # A single failed cover must not abort the batch.
                summary["failed"] += 1
                results.append({**identity, "status": "failed", "reason": str(exc)})
                emit_warn(f"Failed {performer['name']}: {exc}")
            finally:
                with progress_lock:
                    completed += 1
                    emit_progress(completed / len(performers))

    emit_progress(1.0)
    emit_info(
        f"Finished: updated={summary['updated']}, preview={summary['preview']}, "
        f"skipped={summary['skipped']}, failed={summary['failed']}"
    )
    return {
        "apply": options.apply,
        "ffmpeg_path": ffmpeg_path,
        "summary": summary,
        "results": results,
    }


if __name__ == "__main__":
    run_plugin(run)
