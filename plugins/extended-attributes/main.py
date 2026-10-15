from stash_plugin import HookContext, PluginInput, as_bool, as_list, emit_info, run_plugin


def resolve_hook_item_ids(hook: HookContext) -> list[str]:
    if hook.id is None:
        return []

    # Stash fires one hook per entity in a bulk update, and each input carries the whole batch
    # of ids. Only the first hook processes the batch; the rest return immediately.
    batch_ids = [str(batch_id) for batch_id in as_list((hook.input or {}).get("ids"))]
    if not batch_ids:
        return [hook.id]
    return batch_ids if hook.id == batch_ids[0] else []


def run(plugin_input: PluginInput) -> object:
    hook = plugin_input.hook
    dry_run = as_bool(plugin_input.args.get("dry_run"), False)
    item_ids = resolve_hook_item_ids(hook) if hook else None
    if hook and not item_ids:
        emit_info("Skipped: this hook is covered by the first hook of the same batch")
        return {"dry_run": dry_run, "skipped_batch_member": True}

    # Imported only now, so that the hooks of a bulk update that have nothing to do exit fast.
    from attributes import rebuild

    return rebuild(plugin_input, dry_run, item_ids)


if __name__ == "__main__":
    run_plugin(run)
