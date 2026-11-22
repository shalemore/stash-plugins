# Strip Prepended Junk

Removes the image or stray bytes a downloader put in front of a scene's video stream, without
replacing the scene.

Some downloads prepend the cover image or a few bytes to the transport stream. Stash then probes
the prefix and treats the file as a single image, so covers, sprites, and phashes fail. The plugin
checks every scene's `.ts` file for a transport-stream sync pattern after the start, copies the
stream from there over the same path, and rescans what it repaired.

## Why the scene survives

Stash finds files by path. When the content changes at the same path, the next scan of that file
recalculates its checksum, drops the stale phash, and regenerates covers, sprites, and phashes for
the existing scene. Nothing is deleted in Stash, so the scene keeps its studio, tags, performers,
and every other field.

## Tasks

- **Strip prepended junk**: Checks every scene, repairs the files whose stream starts after a
  prefix, and rescans just those files.
- **Dry Run**: Lists what would be repaired without changing any file.

## Safety

- The stream is copied into a temporary file next to the original, so the final rename is atomic
  on the same filesystem.
- It is only swapped in after FFprobe confirms a video stream, a duration close to the original's,
  and a plausible size. Otherwise the original stays untouched and the failure is logged.
- A leftover `*.strip-tmp.ts` file can only come from a killed run; it is never part of the
  library and can be deleted.
