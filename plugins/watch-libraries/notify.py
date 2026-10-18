# ctypes types what foreign functions return as Any.
# pyright: reportAny=false
"""How Linux and Windows report new files, and on which volumes they can.

Linux reports them through inotify and Windows through ReadDirectoryChangesW. The standard library
wraps neither, so both are called through ctypes. Network shares and FUSE drives can change out of
the machine's sight, so they are not watched.
"""

import os
import sys
import threading
from collections.abc import Mapping
from queue import SimpleQueue
from typing import NamedTuple, Protocol, TypeVar

T = TypeVar("T")

# Filesystems served by another machine or by a program. fuseblk is a local disk read through FUSE,
# such as NTFS on Linux.
REMOTE_FILESYSTEMS = ("fuse", "nfs", "cifs", "smb", "9p", "webdav", "davfs")
WINDOWS_LOCAL_FILESYSTEMS = {"NTFS", "ReFS", "exFAT", "FAT32", "FAT"}


class Event(NamedTuple):
    path: str
    is_dir: bool = False
    # The system dropped events, so files may have been missed.
    overflow: bool = False


Events = SimpleQueue[Event]


class Backend(Protocol):
    def watch(self, root: str) -> bool:
        """Reports new files below the root to the queue. False when it cannot see all of them."""
        ...


def is_remote(fstype: str) -> bool:
    return fstype != "fuseblk" and fstype.startswith(REMOTE_FILESYSTEMS)


def parse_mountinfo(text: str) -> dict[str, str]:
    """Maps the mount points of Linux's /proc/self/mountinfo to their filesystem types."""
    mounts: dict[str, str] = {}
    for line in text.splitlines():
        fields, _, rest = line.partition(" - ")
        if rest and len(points := fields.split(" ")) > 4:
            mounts[points[4].replace("\\040", " ")] = rest.split(" ")[0]
    return mounts


def mount_of(path: str, mounts: Mapping[str, T]) -> T | None:
    """What the mounts say about the one holding the path: the longest mount point above it."""
    inside = os.path.join(path, "")
    above = [point for point in mounts if inside.startswith(os.path.join(point, ""))]
    return mounts[max(above, key=len)] if above else None


def filesystem(path: str) -> tuple[str, bool]:
    """The filesystem that holds the path, and whether it is a local disk."""
    path = os.path.realpath(path)
    if sys.platform == "win32":
        return _windows_filesystem(path)
    with open("/proc/self/mountinfo", encoding="utf-8") as mountinfo:
        fstype = mount_of(path, parse_mountinfo(mountinfo.read())) or "unknown"
    return fstype, not is_remote(fstype)


def open_backend(events: Events) -> Backend | None:
    if sys.platform == "linux":
        return Inotify(events)
    if sys.platform == "win32":
        return DirectoryChanges(events)
    return None


if sys.platform == "linux":
    import ctypes

    IN_CLOSE_WRITE = 0x8
    IN_MOVED_TO = 0x80
    IN_CREATE = 0x100
    IN_Q_OVERFLOW = 0x4000
    IN_IGNORED = 0x8000
    IN_ISDIR = 0x40000000
    # A file is complete once its writer closes it or it is renamed into place, as Syncthing does.
    WATCH_MASK = IN_CLOSE_WRITE | IN_MOVED_TO | IN_CREATE

    class Inotify:
        def __init__(self, events: Events) -> None:
            self._events: Events = events
            self._libc: ctypes.CDLL = ctypes.CDLL(None, use_errno=True)
            fd: int = self._libc.inotify_init1(os.O_CLOEXEC)
            if fd < 0:
                raise OSError(ctypes.get_errno(), "inotify_init1 failed")
            self._fd: int = fd
            self._directories: dict[int, str] = {}
            threading.Thread(target=self._read, daemon=True).start()

        def watch(self, root: str) -> bool:
            # Each directory needs its own watch, and the system limits how many there are.
            complete = True
            for directory, _, _ in os.walk(root):
                wd: int = self._libc.inotify_add_watch(self._fd, os.fsencode(directory), WATCH_MASK)
                if wd < 0:
                    complete = False
                else:
                    self._directories[wd] = directory
            return complete

        def _read(self) -> None:
            while data := os.read(self._fd, 64 * 1024):
                offset = 0
                while offset < len(data):
                    wd, mask, _, length = memoryview(data)[offset : offset + 16].cast("I")
                    name = os.fsdecode(data[offset + 16 : offset + 16 + length].rstrip(b"\0"))
                    offset += 16 + length
                    if mask & IN_Q_OVERFLOW:
                        self._events.put(Event("", overflow=True))
                    elif mask & IN_IGNORED:
                        _ = self._directories.pop(wd, None)
                    elif directory := self._directories.get(wd):
                        path = os.path.join(directory, name)
                        if is_dir := bool(mask & IN_ISDIR):
                            _ = self.watch(path)
                        self._events.put(Event(path, is_dir))


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    DRIVE_REMOVABLE = 2
    DRIVE_FIXED = 3
    FILE_LIST_DIRECTORY = 0x1
    FILE_SHARE_ALL = 0x7
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    # File and directory names cover creations and renames; last write covers files being written.
    NOTIFY_FILTER = 0x1 | 0x2 | 0x10
    NEW_FILE_ACTIONS = {1, 3, 5}  # added, modified, renamed to

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def _windows_filesystem(path: str) -> tuple[str, bool]:
        # The volume root must end with a backslash, as in C:\ or \\server\share\.
        root = os.path.splitdrive(path)[0] + "\\"
        name = ctypes.create_unicode_buffer(64)
        found: int = kernel32.GetVolumeInformationW(root, None, 0, None, None, None, name, 64)
        fstype = name.value if found else "unknown"
        drive_type: int = kernel32.GetDriveTypeW(root)
        # Network shares and drives served by programs, such as WinFsp mounts, are not local.
        local = drive_type in {DRIVE_REMOVABLE, DRIVE_FIXED} and fstype in WINDOWS_LOCAL_FILESYSTEMS
        return fstype, local

    class DirectoryChanges:
        def __init__(self, events: Events) -> None:
            self._events: Events = events

        def watch(self, root: str) -> bool:
            # One handle watches the whole tree.
            handle: int | None = kernel32.CreateFileW(
                root, FILE_LIST_DIRECTORY, FILE_SHARE_ALL, None, OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS, None,
            )  # fmt: skip
            if handle in {None, INVALID_HANDLE_VALUE}:
                return False
            threading.Thread(target=self._read, args=(root, handle), daemon=True).start()
            return True

        def _read(self, root: str, handle: int) -> None:
            buffer = ctypes.create_string_buffer(64 * 1024)
            size = wintypes.DWORD()
            while kernel32.ReadDirectoryChangesW(
                handle, buffer, len(buffer), True, NOTIFY_FILTER, ctypes.byref(size), None, None
            ):
                if not size.value:
                    self._events.put(Event("", overflow=True))
                    continue
                data = buffer.raw[: size.value]
                offset = 0
                while True:
                    step, action, length = memoryview(data)[offset : offset + 12].cast("I")
                    name = data[offset + 12 : offset + 12 + length].decode("utf-16-le")
                    if action in NEW_FILE_ACTIONS:
                        path = os.path.join(root, name)
                        self._events.put(Event(path, os.path.isdir(path)))
                    if not step:
                        break
                    offset += step
