# Gallery Cluster

Automatically groups images with similar `details` descriptions into Stash galleries, including single-image galleries, and keeps gallery metadata in sync.

## Settings

Configure these in **Stash → Settings → Plugins → Gallery Cluster**:

- **Source tag**: Tag name or ID. Only ungrouped images carrying this tag are grouped, so the plugin never touches the rest of the library. Leave empty to skip all images.
- **Gallery tag**: Tag name or ID applied to managed galleries. The plugin creates it if a name is given and does not exist yet.
- **Similarity threshold**: Minimum normalized character 3-gram Jaccard similarity (`0` to `1`, default `0.80`) required for an image to join an existing managed gallery. Set to `1` for exact normalized matches only. Stash has no default value for plugin settings, so an empty field or `0` means `0.80`.

## Behavior

- **Grouping (`Image.Create.Post`, `Image.Update.Post`)**: Tagged images with a non-empty `details` description that are not `organized` and not yet in a managed gallery are matched against existing unorganized managed galleries. If none meets the threshold, a new gallery is created for the image. Editing an image that already sits in a managed gallery re-derives that gallery's metadata.
- **Matching**: Only gallery `details` are compared, never image-to-image, so an image joins the first gallery whose description is similar enough. Prompts that only add or drop a clause still match: besides the threshold, a prompt also qualifies when it contains at least 90% of the other one's n-grams and the similarity is at least `0.65`, so rewriting one sentence of a prompt does not split a batch. The gallery `details` follow the latest image that has a description.
- **Derived metadata**: Each unorganized managed gallery derives:
  - `title`, `details`, and `cover` from the latest image, so the gallery presents its newest variant
  - `date` from the earliest image
  - `performers` from the union of every image's performers
  - `studio` from the images, only when all of them share one studio; a gallery loses its studio when they disagree
  - `rating` from the average of the ratings that exist, rounded to the step the UI can show (20 for whole stars, 10 for half stars, 5 for quarter stars, 10 for a 10-point scale, 1 for percent)
- **Manual lock (`organized`)**: Images or galleries marked `organized` are left untouched by automatic clustering and metadata syncing.
- **Cleanup (`Image.Destroy.Post`, `Gallery.Update.Post`)**: Drops images that no longer carry the source tag from their managed gallery, then deletes unorganized managed galleries that end up with `0` images.

## Tasks

- **Dry Run**: Preview how many tagged images would be grouped and how the galleries would change, without modifying Stash.
- **Full Rebuild**: Regroup every tagged image from scratch with the current rules. A group takes over the managed gallery that already holds most of its images, so gallery IDs survive; groups without one get a new gallery, and galleries no group wants are deleted. Images inside organized galleries are left alone, but manual edits to managed gallery titles, dates, and descriptions are lost.

## Changing settings later

Managed galleries are found by the **Gallery tag**, so renaming the source tag only changes which images are picked up. Pointing the plugin at a different gallery tag leaves the galleries carrying the old tag untouched: they keep their images and derived metadata, and stop being managed.
