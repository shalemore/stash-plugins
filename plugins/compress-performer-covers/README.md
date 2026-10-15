# Compress Performer Covers

Compresses performer covers above a maximum size, 100 KiB by default, to AVIF with the FFmpeg
that Stash uses. It aims for the best-looking cover that fits.

## Usage

- **Automatically:** when a performer gets a new cover, the plugin queues a background job that
  compresses it. Saving does not wait for it; the job shows up under `Settings -> Tasks`.
- **For every performer:** run `Compress performer covers` from `Settings -> Tasks -> Plugin
  Tasks`. Covers already within the size are skipped.

The only setting, `Maximum cover size (KiB)`, is under `Settings -> Plugins`.

## How it compresses

Covers wider than 720px are scaled down, then encoded with libaom's all-intra mode, its
image-quality tuning (libaom 3.12 or newer) and full chroma. The plugin picks the best quality
that fits. If that quality would be poor, it tries slightly smaller covers instead.

In a benchmark on real covers on a 2-core server, this took about 2 to 3 seconds per cover and
looked better, by SSIMULACRA2, than the 20 seconds per cover of the plugin's previous encoder.

## Requirements

Only Python 3 and FFmpeg with the `libaom-av1` encoder, which the official Docker image includes.
The plugin needs no Python packages.
