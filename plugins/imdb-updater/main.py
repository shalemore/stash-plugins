"""Links scenes in [imdbid=ttXXXX] folders to IMDb groups and fills TV episode metadata.

A scan runs the hook for every scene it creates. The hook only queues the Sync All task as a Stash
job, once, so the scan does not wait for the scrapers or the IMDb dataset and ends with a single
job that handles every new scene.
"""

from stash_plugin import HookContext, PluginInput, StashClient, emit_info, queue_task, run_plugin

PLUGIN_ID = "imdb-updater"
# The task the hook queues, as named in the manifest.
TASK_NAME = "Sync All"
# Fixed, so that a scan queues only one job while it waits to start.
JOB_DESCRIPTION = "Sync new scenes with IMDb after a scan"


def queue_sync(plugin_input: PluginInput, hook: HookContext) -> object:
    if hook.id is None:
        return None

    from stash_api import find_scenes

    client = StashClient.connect(plugin_input)
    # Scenes outside IMDb folders queue nothing.
    if not find_scenes(client, [hook.id]):
        return None
    queued = queue_task(client, PLUGIN_ID, TASK_NAME, JOB_DESCRIPTION)
    if queued:
        emit_info(f"Queued: {JOB_DESCRIPTION}")
    return {"queued": queued}


def run(plugin_input: PluginInput) -> object:
    if hook := plugin_input.hook:
        return queue_sync(plugin_input, hook)

    from sync_logic import sync_all

    return sync_all(plugin_input)


if __name__ == "__main__":
    run_plugin(run)
