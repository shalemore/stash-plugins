from stash_plugin import PluginInput, StashClient, as_bool, emit_info, emit_warn, run_plugin


def run(plugin_input: PluginInput) -> object:
    hook = plugin_input.hook
    # Only galleries that a scan creates or updates are synced. Edits in the UI carry an input.
    if hook and hook.input is not None:
        return None

    # Imported only now, so that the hooks of edits in the UI exit fast.
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
