"""Starts and controls the watcher, which keeps running after the task that started it ends.

The watcher holds a lock on a file for as long as it runs, and the system releases the lock when
the process ends, however it ends. A task asks the watcher to stop by creating a file that it
looks for every second.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import BinaryIO

from stash_plugin import PluginInput

WATCHER = Path(__file__).with_name("watcher.py")
STOP = "stop"


def state_directory(plugin_input: PluginInput) -> Path:
    import tempfile

    directory = Path(plugin_input.config_dir or tempfile.gettempdir()) / "watch-libraries"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def lock(state_dir: Path) -> BinaryIO | None:
    """Takes the watcher's lock, or returns None when a running watcher holds it."""
    file = (state_dir / "watcher.lock").open("a+b")
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        file.close()
        return None
    return file


def is_running(state_dir: Path) -> bool:
    if held := lock(state_dir):
        held.close()
        return False
    return True


def take_request(state_dir: Path, name: str) -> bool:
    try:
        (state_dir / name).unlink()
    except FileNotFoundError:
        return False
    return True


def start(plugin_input: PluginInput) -> str:
    state_dir = state_directory(plugin_input)
    if is_running(state_dir):
        return "The watcher is already running"
    _ = take_request(state_dir, STOP)

    connection: dict[str, object] = {"GraphQLURL": plugin_input.graphql_url}
    if plugin_input.config_dir:
        connection["Dir"] = plugin_input.config_dir
    if plugin_input.cookie:
        name, _, value = plugin_input.cookie.partition("=")
        connection["SessionCookie"] = {"Name": name, "Value": value}

    # Detached, the watcher outlives the task's process.
    watcher = subprocess.Popen(
        [sys.executable, str(WATCHER)],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=sys.platform != "win32",
        creationflags=(
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            if sys.platform == "win32"
            else 0
        ),
    )
    assert watcher.stdin
    with watcher.stdin:
        _ = watcher.stdin.write(json.dumps({"server_connection": connection}).encode())
    return f"Started the watcher; its log is {state_dir / 'watcher.log'}"


def stop(plugin_input: PluginInput) -> str:
    state_dir = state_directory(plugin_input)
    if not is_running(state_dir):
        return "The watcher is not running"
    (state_dir / STOP).touch()
    return "Asked the watcher to stop"
