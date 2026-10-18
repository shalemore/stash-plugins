# Watch Libraries

Scans new files in your libraries as they appear, instead of scanning whole libraries. Stash's own
scan walks every file of a library, which is slow on a large one.

## Usage

Run `Start watcher` from `Settings -> Tasks -> Plugin Tasks`. The watcher keeps running in the
background until you run `Stop watcher` or Stash restarts, so run `Start watcher` again after a
restart; running it while the watcher is up does nothing.

The watcher reads your libraries, their `Videos` and `Images` switches, the file extensions and the
exclude patterns from Stash, and picks up changes to them within a minute. It scans with the
default scan options of `Settings -> Tasks`. Its log is `watch-libraries/watcher.log` in Stash's
config directory.

## Which libraries are watched

Only libraries on a local disk of Linux or Windows, whose system reports new files as they are
written or renamed into place, as Syncthing and aria2 do. Linux reports them through inotify,
Windows through ReadDirectoryChangesW on NTFS, ReFS, exFAT and FAT.

Network shares and FUSE drives, such as a mounted 115 or OneDrive, can change on the other side
without the system noticing, so they are not watched; scan them with Stash as before. The log
lists each library and whether it is watched.

## Batching

New files are collected until none has appeared for `Quiet time`, 10 seconds by default, or until
`Longest wait`, 120 seconds, has passed since the first one. Then they are scanned as one Stash
job. Meanwhile:

- A file that is still being written, or that has a `.aria2` file beside it, waits for the next
  batch.
- While a scan is queued or running, new files keep collecting for the next one.
- More than 200 new files in one directory are scanned as that directory. Stash skips the files it
  already knows.

## Requirements

Python 3, as in the official Docker image; the plugin needs no Python packages. When Stash has
authentication on, the watcher needs the API key in Stash's `config.yml`, which it reads itself.
