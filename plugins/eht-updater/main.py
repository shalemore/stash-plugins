"""Fills gallery metadata from E-Hentai, using the gid_token folder names of galleries.

A scan runs the hook for every gallery it creates or updates. The hook only queues the task that
updates unorganized galleries as a Stash job, once, so the scan does not wait for E-Hentai and
ends with a single job that handles every new gallery.
"""

from stash_plugin import (
    PluginInput,
    StashClient,
    as_bool,
    emit_info,
    emit_warn,
    queue_task,
    run_plugin,
)

PLUGIN_ID = "eht-updater"
# The task the hook queues, as named in the manifest.
TASK_NAME = "Update Unorganized Galleries"
# Fixed, so that a scan queues only one job while it waits to start.
JOB_DESCRIPTION = "Update unorganized E-Hentai galleries after a scan"


def queue_update(plugin_input: PluginInput) -> object:
    client = StashClient.connect(plugin_input, timeout=30)
    queued = queue_task(client, PLUGIN_ID, TASK_NAME, JOB_DESCRIPTION)
    if queued:
        emit_info(f"Queued: {JOB_DESCRIPTION}")
    return {"queued": queued}


def run(plugin_input: PluginInput) -> object:
    if hook := plugin_input.hook:
        # Only scans are synced. Edits in the UI carry an input.
        return queue_update(plugin_input) if hook.input is None else None

    from sync_logic import Taxonomy, collect_targets, process_targets, select_target_galleries

    args = plugin_input.args
    client = StashClient.connect(plugin_input, timeout=30)
    dry_run = as_bool(args.get("dry_run"), False)
    emit_info(
        f"Starting E-Hentai Gallery Updater dry_run={dry_run} "
        f"skip_organized={args.get('skip_organized', True)}"
    )
    taxonomy = Taxonomy.load(client, dry_run)
    galleries = select_target_galleries(client, plugin_input)
    emit_info(f"Loaded {len(galleries)} gallery candidates from Stash")
    targets, skipped = collect_targets(galleries)
    emit_info(f"Selected {len(targets)} eligible galleries; {len(skipped)} galleries were skipped")
    for item in skipped:
        emit_info(f"Skipped: gallery_id={item['id']} folder={item['path']} reason={item['reason']}")
    results, failed = process_targets(taxonomy, targets)

    emit_info(
        f"Task finished: {len(results)} succeeded, {len(skipped)} skipped, {len(failed)} failed"
    )
    for item in failed:
        emit_warn(f"Failed: gallery_id={item['id']} folder={item['path']} reason={item['reason']}")
    return {
        "dry_run": dry_run,
        "success": len(results),
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }


if __name__ == "__main__":
    run_plugin(run)
