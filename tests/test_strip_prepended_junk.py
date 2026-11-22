# pyright: reportAny=false, reportUnusedCallResult=false
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from catalog import PLUGINS_DIR

PLUGIN = PLUGINS_DIR / "strip-prepended-junk"
PACKET = 188


def load(name: str) -> ModuleType:
    # Each plugin ships its own stash_api/media, so drop the previous ones before loading.
    for module_name in ("stash_api", "stash_plugin", "media"):
        sys.modules.pop(module_name, None)
    sys.path.insert(0, str(PLUGIN))
    try:
        spec = importlib.util.spec_from_file_location(f"strip_junk_{name}", PLUGIN / f"{name}.py")
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(PLUGIN))


media = load("media")


def write_stream(path: Path, prefix: bytes = b"", packets: int = 5) -> Path:
    data = bytearray(prefix)
    for _ in range(packets):
        data.extend(b"\x47" + bytes(PACKET - 1))
    path.write_bytes(bytes(data))
    return path


def test_find_prepended_prefix_ignores_a_file_that_starts_with_the_stream(tmp_path: Path):
    assert media.find_prepended_prefix(write_stream(tmp_path / "clean.ts")) is None


def test_find_prepended_prefix_reports_stray_bytes(tmp_path: Path):
    prefix = media.find_prepended_prefix(write_stream(tmp_path / "junk.ts", b"\x42\x60\x82"))
    assert prefix is not None
    assert prefix.length == 3
    assert prefix.signature == b"\x42\x60\x82"


def test_find_prepended_prefix_reports_a_prepended_image(tmp_path: Path):
    png = b"\x89PNG\r\n\x1a\n" + bytes(62)
    prefix = media.find_prepended_prefix(write_stream(tmp_path / "image.ts", png))
    assert prefix is not None
    assert prefix.length == 70


def test_find_prepended_prefix_ignores_files_without_a_stream(tmp_path: Path):
    path = tmp_path / "not-a-stream.ts"
    path.write_bytes(b"\x00" * 4096)
    assert media.find_prepended_prefix(path) is None


def test_copy_from_keeps_the_stream_bytes(tmp_path: Path):
    source = write_stream(tmp_path / "junk.ts", b"\x42\x60\x82")
    target = tmp_path / "out.ts"
    media.copy_from(source, target, 3)

    assert target.read_bytes() == source.read_bytes()[3:]
    assert media.find_prepended_prefix(target) is None


def test_is_acceptable_requires_a_plausible_video():
    original = media.MediaInfo(duration=100.0, has_video=True, format_name="mpegts")
    good = media.MediaInfo(duration=99.5, has_video=True, format_name="mpegts")
    short = media.MediaInfo(duration=90.0, has_video=True, format_name="mpegts")
    silent = media.MediaInfo(duration=100.0, has_video=False, format_name="mpegts")
    unknown = media.MediaInfo(duration=None, has_video=True, format_name="")

    assert media.is_acceptable(original, good, 1.0)
    assert not media.is_acceptable(original, short, 1.0)
    assert not media.is_acceptable(original, silent, 1.0)
    assert not media.is_acceptable(original, good, 0.3)
    assert not media.is_acceptable(original, unknown, 1.0)


def test_is_acceptable_without_an_original_duration_still_checks_the_result():
    repaired = media.MediaInfo(duration=30.0, has_video=True, format_name="mpegts")
    assert media.is_acceptable(None, repaired, 1.0)
    assert not media.is_acceptable(None, repaired, 0.2)


class FakeSceneClient:
    def __init__(self, payload: object) -> None:
        self.payload: object = payload

    def execute(self, _operation: object, _variables: object) -> object:
        return self.payload


def test_scene_ts_files_keeps_only_ts_video_files():
    payload: dict[str, object] = {
        "findScenes": {
            "scenes": [
                {"id": "1", "files": [{"path": "/data/a.ts"}]},
                {"id": "2", "files": [{"path": "/data/b.mp4"}, {"path": "/data/c.vtt"}]},
                {"id": "3", "files": [{"path": "/data/d.TS"}]},
                {"id": "4", "files": []},
            ]
        }
    }
    main = load("main")

    paths = [path.as_posix() for path in main.scene_ts_files(FakeSceneClient(payload))]

    assert paths == ["/data/a.ts", "/data/d.TS"]
