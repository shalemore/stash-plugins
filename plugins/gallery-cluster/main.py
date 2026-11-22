"""Entrypoint for the Gallery Cluster plugin."""

from stash_plugin import HookContext, PluginInput, as_bool, as_list, as_str, emit_info, run_plugin

RELEVANT_IMAGE_FIELDS = frozenset(
    {"details", "title", "date", "code", "organized", "paths", "galleries", "tag_ids"}
)
RELEVANT_GALLERY_FIELDS = frozenset({"image_ids", "organized", "cover_image_id"})


def resolve_hook_item_ids(hook: HookContext) -> list[str]:
    if hook.id is None:
        return []

    # Stash fires one hook per entity in a bulk update, carrying all IDs in `ids`.
    # Only the first hook processes the batch; the remaining hooks exit immediately.
    batch_ids = [str(batch_id) for batch_id in as_list((hook.input or {}).get("ids"))]
    if not batch_ids:
        return [hook.id]
    return batch_ids if hook.id == batch_ids[0] else []


def should_skip_hook(hook: HookContext, entity_type: str | None) -> bool:
    if not hook.input_fields:
        return False
    fields = set(hook.input_fields)
    if entity_type == "image":
        return not (fields & RELEVANT_IMAGE_FIELDS)
    if entity_type == "gallery":
        return not (fields & RELEVANT_GALLERY_FIELDS)
    return False


def run(plugin_input: PluginInput) -> object:
    hook = plugin_input.hook
    dry_run = as_bool(plugin_input.args.get("dry_run"), False)
    entity_type = as_str(plugin_input.args.get("entity_type"))

    item_ids: list[str] | None = None
    if hook is not None:
        if entity_type != "image_destroy":
            item_ids = resolve_hook_item_ids(hook)
            if not item_ids:
                emit_info("Skipped: covered by the first hook of the same batch")
                return {"dry_run": dry_run, "skipped_batch_member": True}
        if should_skip_hook(hook, entity_type):
            return {"dry_run": dry_run, "skipped_irrelevant_fields": True}

    from cluster import run_cluster

    return run_cluster(
        plugin_input,
        dry_run=dry_run,
        entity_type=entity_type,
        item_ids=item_ids,
    )


if __name__ == "__main__":
    run_plugin(run)
