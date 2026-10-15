"""Compresses performer covers above a size limit to AVIF.

Saving a performer runs the hook, which only queues the compression as a Stash job, so the save
does not wait for the encoder. The job, or the task run from the Tasks page for every performer,
does the work.
"""

from stash_plugin import (
    HookContext,
    PluginInput,
    StashClient,
    as_str,
    emit_info,
    queue_task,
    run_plugin,
)

PLUGIN_ID = "compress-performer-covers"
# The task that compresses covers, as named in the manifest.
TASK_NAME = "Compress performer covers"
DEFAULT_TARGET_KB = 100


def target_bytes(settings: dict[str, object]) -> int:
    raw = settings.get("target_kb")
    if raw is None or raw == "":
        return DEFAULT_TARGET_KB * 1024
    try:
        value = int(raw) if isinstance(raw, int | float | str) else int(str(raw))
    except ValueError as exc:
        raise ValueError(f"The maximum cover size must be a number; received: {raw}") from exc
    if value < 1:
        raise ValueError(f"The maximum cover size must be at least 1 KiB; received: {value}")
    return value * 1024


def data_url_size(image: str) -> int | None:
    """The size of an image sent inline as a base64 data URL, as the Stash UI sends uploads."""
    if not image.startswith("data:") or ";base64," not in image:
        return None
    encoded = image.partition(";base64,")[2]
    return len(encoded) * 3 // 4 - encoded.count("=", -2)


def queue_compression(plugin_input: PluginInput, hook: HookContext) -> object:
    image = as_str((hook.input or {}).get("image"))
    # Saving without a new cover, or removing it, leaves nothing to compress.
    if not image or hook.id is None:
        return None

    from stash_api import get_runtime_config

    client = StashClient.connect(plugin_input)
    _, settings = get_runtime_config(client)
    size = data_url_size(image)
    # This also skips the cover that the job itself writes back.
    if size is not None and size <= target_bytes(settings):
        return None

    description = f"Compress the cover of performer {hook.id}"
    if queue_task(client, PLUGIN_ID, TASK_NAME, description, {"performer_id": hook.id}):
        emit_info(f"Queued: {description}")
    return {"queued": hook.id}


def run(plugin_input: PluginInput) -> object:
    if hook := plugin_input.hook:
        return queue_compression(plugin_input, hook)

    from covers import compress_covers
    from stash_api import get_runtime_config

    client = StashClient.connect(plugin_input)
    ffmpeg, settings = get_runtime_config(client)
    performer_id = as_str(plugin_input.args.get("performer_id"))
    return compress_covers(client, ffmpeg, target_bytes(settings), performer_id)


if __name__ == "__main__":
    run_plugin(run)
