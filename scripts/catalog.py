"""Reads the plugins in plugins/ and describes each one for the index and the release plan."""

import re
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import yaml

ROOT = Path(__file__).resolve().parents[1]
PLUGINS_DIR = ROOT / "plugins"
VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")


@dataclass(frozen=True)
class Plugin:
    id: str
    name: str
    description: str
    version: str
    directory: Path

    @property
    def tag(self) -> str:
        """The Git tag that marks this version as released."""
        return f"{self.id}-v{self.version}"

    @property
    def root(self) -> Path:
        """The repository the plugin is in."""
        return self.directory.parents[1]

    @property
    def sources(self) -> list[Path]:
        """What the plugin's zip is built from: its directory and the modules every plugin
        imports, which the build copies into each plugin."""
        return [self.root / "shared", self.directory]


def load_plugin(directory: Path) -> Plugin:
    manifest = directory / f"{directory.name}.yml"
    data = cast(object, yaml.safe_load(manifest.read_text(encoding="utf-8")))
    fields = cast("dict[object, object]", data) if isinstance(data, dict) else {}

    def text(key: str) -> str:
        value = fields.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{manifest}: {key} must be a non-empty string")
        return value.strip()

    version = text("version")
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"{manifest}: version must look like 1.2.3, got {version!r}")
    return Plugin(
        id=directory.name,
        name=text("name"),
        description=text("description"),
        version=version,
        directory=directory,
    )


def load_plugins() -> list[Plugin]:
    """Every plugin directory that contains a manifest named after the directory."""
    return [
        load_plugin(directory)
        for directory in sorted(PLUGINS_DIR.iterdir())
        if directory.is_dir() and (directory / f"{directory.name}.yml").is_file()
    ]


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout


def existing_tags(root: Path = ROOT) -> set[str]:
    return set(git(root, "tag", "--list").decode().split())


def last_modified(plugin: Plugin, ref: str) -> datetime:
    """When the last commit up to the ref that touched the plugin's sources was made, in UTC."""
    paths = [str(path.relative_to(plugin.root)) for path in plugin.sources]
    timestamp = git(plugin.root, "log", "-n", "1", "--format=%ct", ref, "--", *paths).strip()
    return datetime.fromtimestamp(int(timestamp), UTC) if timestamp else datetime.now(UTC)
