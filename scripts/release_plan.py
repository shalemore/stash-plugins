"""Finds the plugin versions that have not been released yet.

A version is released once its tag `<id>-v<version>` exists. Bumping `version` in a plugin's
manifest is therefore what releases that plugin.

Usage: `python scripts/release_plan.py` prints `key=value` lines for $GITHUB_OUTPUT.
"""

import json
from collections.abc import Iterable

from catalog import Plugin, existing_tags, load_plugins


def unreleased(plugins: Iterable[Plugin], tags: set[str]) -> list[Plugin]:
    return [plugin for plugin in plugins if plugin.tag not in tags]


if __name__ == "__main__":
    pending = unreleased(load_plugins(), existing_tags())
    print(f"release={str(bool(pending)).lower()}")
    plugins = [{"id": plugin.id, "version": plugin.version} for plugin in pending]
    print(f"plugins={json.dumps(plugins, separators=(',', ':'))}")
