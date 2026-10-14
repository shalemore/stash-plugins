"""Builds the plugin source index that Stash reads.

Usage: `python scripts/build_site.py [output]` writes `index.yml` and one `<id>.zip` per plugin
to the output directory, `_site` by default. Each zip holds the plugin's directory.

Stash offers an update whenever a plugin's date in the index moves forward, whatever its version.
So a plugin whose version is already released is built from the commit that its tag points to,
which keeps its zip and date exactly as released, even after later commits change its files.
Only a version without a tag is built from the working tree.
"""

import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

import yaml

from catalog import Plugin, existing_tags, git, last_modified, load_plugins

FIXED_TIME = (1980, 1, 1, 0, 0, 0)


def working_files(plugin: Plugin) -> dict[str, bytes]:
    return {
        file.relative_to(source).as_posix(): file.read_bytes()
        for source in plugin.sources
        for file in source.rglob("*")
        if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc"
    }


def released_files(plugin: Plugin) -> dict[str, bytes]:
    """The plugin's files at the commit that its version's tag points to."""
    files: dict[str, bytes] = {}
    for source in plugin.sources:
        prefix = f"{source.relative_to(plugin.root).as_posix()}/"
        listing = git(plugin.root, "ls-tree", "-r", "--name-only", plugin.tag, "--", prefix)
        for name in listing.decode().splitlines():
            files[name.removeprefix(prefix)] = git(plugin.root, "show", f"{plugin.tag}:{name}")
    return files


def zip_files(files: dict[str, bytes], target: Path) -> None:
    """Zips the files reproducibly, so the same files always give the same checksum."""
    with zipfile.ZipFile(target, "w") as archive:
        for name, content in sorted(files.items()):
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, content)


def build(output: Path, plugins: list[Plugin], tags: set[str]) -> None:
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)

    index: list[dict[str, object]] = []
    for plugin in plugins:
        released = plugin.tag in tags
        archive = output / f"{plugin.id}.zip"
        zip_files(released_files(plugin) if released else working_files(plugin), archive)
        date = last_modified(plugin, plugin.tag if released else "HEAD")
        index.append(
            {
                "id": plugin.id,
                "name": plugin.name,
                "metadata": {"description": plugin.description},
                "version": plugin.version,
                "date": date.strftime("%Y-%m-%d %H:%M:%S"),
                "path": archive.name,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            }
        )
    index_yaml = yaml.safe_dump(index, sort_keys=False, allow_unicode=True)
    _ = (output / "index.yml").write_text(index_yaml, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("_site")
    build(output, load_plugins(), existing_tags())
