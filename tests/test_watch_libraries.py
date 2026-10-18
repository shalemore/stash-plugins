# pyright: reportAny=false, reportUnusedCallResult=false
import importlib.util
import os
import sys
from types import ModuleType

from catalog import PLUGINS_DIR

PLUGIN = PLUGINS_DIR / "watch-libraries"


def load(name: str) -> ModuleType:
    # The plugin's modules import each other by name, as Stash runs them from their directory.
    sys.path.insert(0, str(PLUGIN))
    try:
        spec = importlib.util.spec_from_file_location(
            f"watch_libraries_{name}", PLUGIN / f"{name}.py"
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(PLUGIN))


debounce = load("debounce")
libraries = load("libraries")
notify = load("notify")


def path(*parts: str) -> str:
    return os.path.join(os.sep, *parts)


def test_waits_for_quiet():
    debouncer = debounce.Debouncer(quiet=10, max_wait=120)
    debouncer.add(path("a", "1.jpg"), 0)
    debouncer.add(path("a", "2.jpg"), 8)
    assert not debouncer.is_due(17)
    assert debouncer.is_due(18)
    assert debouncer.take() == [path("a", "1.jpg"), path("a", "2.jpg")]
    assert not debouncer.is_due(1000)


def test_a_steady_stream_is_scanned_after_the_longest_wait():
    debouncer = debounce.Debouncer(quiet=10, max_wait=120)
    for second in range(0, 125, 5):
        debouncer.add(path("a", f"{second}.jpg"), second)
    assert debouncer.is_due(120)


def test_collapse_keeps_few_files_and_replaces_many_by_their_directory():
    few = [path("lib", "a", "1.jpg"), path("lib", "a", "2.jpg")]
    many = [path("lib", "b", f"{index}.jpg") for index in range(5)]
    assert debounce.collapse([*few, *many], limit=3) == [*few, path("lib", "b")]


def test_collapse_drops_paths_inside_a_collapsed_directory():
    nested = [path("lib", "b", "c", f"{index}.jpg") for index in range(5)]
    outer = [path("lib", "b", f"{index}.jpg") for index in range(5)]
    extra = path("lib", "b", "d", "x.jpg")
    assert debounce.collapse([*nested, *outer, extra], limit=3) == [path("lib", "b")]


MOUNTINFO = (
    "20 1 8:1 / / rw - ext4 /dev/sda1 rw\n"
    "40 20 0:50 / /data/openlist/115open rw,relatime shared:1 - fuse CloudFS rw\n"
    "41 20 8:17 / /data/stash rw - ext4 /dev/sdb1 rw\n"
    "42 20 8:33 / /data/my\\040disk rw - fuseblk /dev/sdc1 rw\n"
    "43 20 0:60 / /data/rclone rw - fuse.rclone remote: rw\n"
)


def test_linux_mounts():
    mounts = notify.parse_mountinfo(MOUNTINFO)
    assert mounts["/data/my disk"] == "fuseblk"
    if os.sep == "/":
        assert notify.mount_of("/data/openlist/115open/IMDB", mounts) == "fuse"
        assert notify.mount_of("/data/stash/remote/img", mounts) == "ext4"
        assert notify.mount_of("/data/stashed", mounts) == "ext4"


def test_remote_filesystems():
    remote = ["fuse", "fuse.rclone", "nfs4", "cifs", "smb3", "webdav"]
    local = ["ext4", "btrfs", "xfs", "fuseblk", "overlay"]
    assert [fstype for fstype in remote + local if notify.is_remote(fstype)] == remote


def make_rules():
    return libraries.Rules.build(["mp4"], [".JPG", "png"], [], [r"[/\\]@eaDir[/\\]"])


def test_rules_follow_the_library_flags_and_extensions():
    rules = make_rules()
    video = libraries.Library(path("v"), True, False)
    image = libraries.Library(path("i"), False, True)
    assert rules.wants(video, path("v", "a.MP4"))
    assert not rules.wants(video, path("v", "a.jpg"))
    assert rules.wants(image, path("i", "a.jpg"))
    assert not rules.wants(image, path("i", "a.mp4"))
    assert not rules.wants(image, path("i", "@eaDir", "a.jpg"))
    assert not rules.wants(image, path("i", ".syncthing.a.jpg.tmp"))
