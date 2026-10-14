import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from build_site import build, working_files, zip_files
from catalog import existing_tags, load_plugin
from release_plan import unreleased


def write_plugin(root: Path, version: str = "1.0.0", code: str = "print('hi')\n") -> Path:
    directory = root / "plugins" / "demo"
    (directory / "__pycache__").mkdir(parents=True, exist_ok=True)
    manifest = f"name: Demo\ndescription: A demo\nversion: {version}\n"
    _ = (directory / "demo.yml").write_text(manifest, encoding="utf-8")
    _ = (directory / "main.py").write_bytes(code.encode())
    _ = (directory / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"\x00")
    return directory


def commit(root: Path, timestamp: int, tag: str | None = None) -> None:
    date = f"{timestamp} +0000"
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}

    def git(*args: str) -> None:
        command = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", *args]
        _ = subprocess.run(command, cwd=root, env=env, check=True, capture_output=True)

    if not (root / ".git").exists():
        git("init", "-q")
    git("add", "plugins/demo/demo.yml", "plugins/demo/main.py")
    git("commit", "-q", "-m", "change")
    if tag:
        git("tag", tag)


def build_site(root: Path, output: Path) -> tuple[str, bytes]:
    """Builds the site and returns its index and the demo plugin's main.py."""
    build(output, [load_plugin(root / "plugins" / "demo")], existing_tags(root))
    with zipfile.ZipFile(output / "demo.zip") as archive:
        return (output / "index.yml").read_text(encoding="utf-8"), archive.read("main.py")


def test_zip_is_reproducible_and_skips_bytecode(tmp_path: Path):
    files = working_files(load_plugin(write_plugin(tmp_path)))
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    zip_files(files, first)
    zip_files(files, second)

    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["demo.yml", "main.py"]


def test_a_released_version_is_built_as_tagged(tmp_path: Path):
    _ = write_plugin(tmp_path, code="print('released')\n")
    commit(tmp_path, 1_700_000_000, tag="demo-v1.0.0")
    released_index, released_code = build_site(tmp_path, tmp_path / "released")

    # Stash offers an update whenever the date moves forward, so a later change without a
    # version bump must leave the plugin's entry and zip as they were.
    _ = write_plugin(tmp_path, code="print('unreleased')\n")
    commit(tmp_path, 1_800_000_000)
    index, code = build_site(tmp_path, tmp_path / "rebuilt")

    assert (index, code) == (released_index, released_code)
    assert "date: '2023-11-14 22:13:20'" in index
    assert code == b"print('released')\n"


def test_a_new_version_is_built_from_the_working_tree(tmp_path: Path):
    _ = write_plugin(tmp_path)
    commit(tmp_path, 1_700_000_000, tag="demo-v1.0.0")
    _ = write_plugin(tmp_path, version="1.1.0", code="print('new')\n")
    commit(tmp_path, 1_800_000_000)

    index, code = build_site(tmp_path, tmp_path / "site")

    assert "version: 1.1.0" in index
    assert "date: '2027-01-15 08:00:00'" in index
    assert code == b"print('new')\n"


def test_version_must_be_a_full_version(tmp_path: Path):
    with pytest.raises(ValueError, match="version"):
        _ = load_plugin(write_plugin(tmp_path, "1.0"))


def test_a_version_without_a_tag_is_unreleased(tmp_path: Path):
    plugin = load_plugin(write_plugin(tmp_path))

    assert unreleased([plugin], set()) == [plugin]
    assert unreleased([plugin], {"demo-v0.9.0"}) == [plugin]
    assert unreleased([plugin], {"demo-v1.0.0"}) == []
