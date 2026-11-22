"""Removes the prefix that hides a scene's transport stream, keeping the same scene.

The stream starts at a later offset, so Stash probes the prefix (usually a small image) instead
of the video. Rewriting the file at the same path is what keeps the scene: Stash matches files by
path, sees the changed modification time, recalculates the checksum, drops the stale phash, and
regenerates covers, sprites, and phashes. Nothing is deleted in Stash, so tags, studio, performers,
and every other field stay.
"""

from __future__ import annotations

import os
from pathlib import Path

from media import (
    MediaInfo,
    PrependedPrefix,
    copy_from,
    find_prepended_prefix,
    is_acceptable,
    probe_media,
)
from stash_api import find_scene_files, scan_paths
from stash_plugin import (
    PluginInput,
    Progress,
    StashClient,
    as_bool,
    emit_info,
    emit_warn,
    run_plugin,
)

TEMP_SUFFIX = ".strip-tmp.ts"
TS_SUFFIX = ".ts"


def scene_ts_files(client: StashClient) -> list[Path]:
    """The video files of every scene that could carry a hidden transport stream."""
    return [
        Path(path)
        for _scene_id, path in find_scene_files(client)
        if path.lower().endswith(TS_SUFFIX)
    ]


def repair_file(source: Path, prefix: PrependedPrefix, *, dry_run: bool) -> bool:
    """Rewrite one file without its prefix; returns whether it was repaired (or would be)."""
    if dry_run:
        return True

    original = probe_media(source, force_ts=True)
    temp = source.with_name(f"{source.name}{TEMP_SUFFIX}")
    try:
        copy_from(source, temp, prefix.length)
        if not accept_repair(source, temp, original):
            return False
        os.replace(temp, source)
        return True
    finally:
        if temp.exists():
            temp.unlink()


def accept_repair(source: Path, temp: Path, original: MediaInfo | None) -> bool:
    repaired = probe_media(temp, force_ts=False)
    if repaired is None:
        emit_warn(f"Repaired file is unreadable, keeping the original: {source.name}")
        return False
    size_ratio = temp.stat().st_size / source.stat().st_size
    if not is_acceptable(original, repaired, size_ratio):
        emit_warn(f"Repaired stream does not match the original, keeping it: {source.name}")
        return False
    return True


def run(plugin_input: PluginInput) -> object:
    dry_run = as_bool(plugin_input.args.get("dry_run"), False)
    client = StashClient.connect(plugin_input)
    candidates = scene_ts_files(client)

    emit_info(
        f"Strip Prepended Junk: checking {len(candidates)} scene files "
        f"({'dry run' if dry_run else 'repairing'})..."
    )

    progress = Progress(len(candidates), label="Strip Prepended Junk: checking scenes")
    found: list[Path] = []
    repaired: list[Path] = []
    failed: list[Path] = []
    for source in candidates:
        try:
            prefix = find_prepended_prefix(source)
            if prefix is not None:
                found.append(source)
                if repair_file(source, prefix, dry_run=dry_run):
                    repaired.append(source)
                    emit_info(
                        f"{'Would strip' if dry_run else 'Stripped'} {prefix.length} bytes "
                        f"from {source}"
                    )
                else:
                    failed.append(source)
        except OSError as exc:
            emit_warn(f"Could not read {source}: {exc}")
            failed.append(source)
        progress.step()

    if not dry_run:
        scan_paths(client, [str(path) for path in repaired])

    summary = {
        "dry_run": dry_run,
        "scanned": len(candidates),
        "with_prefix": len(found),
        "repaired": len(repaired),
        "failed": len(failed),
    }
    emit_info(
        f"Strip Prepended Junk{' dry run' if dry_run else ''}: checked {summary['scanned']} "
        f"scene files, found {summary['with_prefix']} with a prefix before the stream, "
        f"handled {summary['repaired']}, failed {summary['failed']}."
    )
    return summary


if __name__ == "__main__":
    run_plugin(run)
