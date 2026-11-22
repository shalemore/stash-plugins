# Gallery Cluster

Automatically groups images with similar `details` descriptions into Stash galleries, including single-image galleries, and keeps gallery metadata in sync.

## Settings

Configure these in **Stash → Settings → Plugins → Gallery Cluster**:

- **Path prefixes**: Comma-separated path prefixes of images to manage (for example `/media/images/`). Leave empty to skip all images.
- **Gallery tag**: Tag name or ID applied to managed galleries. The plugin creates it if a name is given and does not exist yet.
- **Similarity threshold**: Minimum normalized character 3-gram Jaccard similarity (`0` to `1`, default `0.85`) required for an image to join an existing managed gallery. Set to `1` for exact normalized matches only.

## Behavior

- **Auto-grouping (`Image.Create.Post`, `Image.Update.Post`)**: Matching images with a non-empty `details` description that are not `organized` and not yet in a managed gallery are matched against existing unorganized managed galleries. If none meets the threshold, a new gallery is created.
- **Timeline metadata**: Each unorganized managed gallery derives:
  - `title` from the earliest image's `title`
  - `date` from the latest image's `date`
  - `cover` from the earliest image
- **Manual lock (`organized`)**: Images or galleries marked `organized` are left untouched by automatic clustering and metadata syncing.
- **Cleanup (`Image.Destroy.Post`, `Gallery.Update.Post`)**: Updates remaining gallery metadata when images are removed, and deletes unorganized managed galleries that reach `0` images.

## Tasks

- **Dry Run**: Preview how many ungrouped images would be grouped or synced without modifying Stash.
- **Full Rebuild**: Process all matching ungrouped images and synchronize managed galleries.
