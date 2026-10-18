"""Starts or stops a watcher that scans the new files of the libraries as they appear."""

from stash_plugin import PluginInput, as_str, emit_info, run_plugin


def run(plugin_input: PluginInput) -> object:
    import daemon

    actions = {"start": daemon.start, "stop": daemon.stop}
    mode = as_str(plugin_input.args.get("mode")) or ""
    if mode not in actions:
        raise ValueError(f"Unknown mode: {mode}")
    message = actions[mode](plugin_input)
    emit_info(message)
    return message


if __name__ == "__main__":
    run_plugin(run)
