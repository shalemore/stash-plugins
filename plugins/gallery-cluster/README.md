# Gallery Cluster

Automatically groups images with similar `details` descriptions into Stash galleries, including single-image galleries, and keeps gallery metadata in sync.

## Settings

Configure these in **Stash → Settings → Plugins → Gallery Cluster**:

- **Source tag**: Tag name or ID. Only ungrouped images carrying this tag are grouped, so the plugin never touches the rest of the library. Leave empty to skip all images.
- **Gallery tag**: Tag name or ID applied to managed galleries. The plugin creates it if a name is given and does not exist yet.
- **Similarity threshold**: Minimum normalized character 3-gram Jaccard similarity (`0` to `1`, default `0.85`) required for an image to join an existing managed gallery. Set to `1` for exact normalized matches only. Stash has no default value for plugin settings, so an empty field or `0` means `0.85`.

## Behavior

- **Grouping (`Image.Create.Post`, `Image.Update.Post`)**: Tagged images with a non-empty `details` description that are not `organized` and not yet in a managed gallery are matched against existing unorganized managed galleries. If none meets the threshold, a new gallery is created for the image.
- **Matching**: Only gallery `details` are compared, never image-to-image, so an image joins the first gallery whose description is similar enough. The gallery `details` always come from the first image that created it.
- **Timeline metadata**: Each unorganized managed gallery derives:
  - `title` from the earliest image's `title`
  - `date` from the latest image's `date`
  - `cover` from the earliest image
- **Manual lock (`organized`)**: Images or galleries marked `organized` are left untouched by automatic clustering and metadata syncing.
- **Cleanup (`Image.Destroy.Post`, `Gallery.Update.Post`)**: Updates remaining gallery metadata when images are removed, and deletes unorganized managed galleries that reach `0` images.

## Tasks

- **Dry Run**: Preview how many tagged images would be grouped or synced without modifying Stash.
- **Full Rebuild**: Process all tagged ungrouped images and synchronize managed galleries.

## Changing settings later

Managed galleries are found by the **Gallery tag**, so renaming the source tag only changes which images are picked up. Pointing the plugin at a different gallery tag leaves the galleries carrying the old tag untouched: they keep their images, title, date, and cover, and stop being managed.
