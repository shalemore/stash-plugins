"""Core clustering, gallery metadata synchronization, and lifecycle cleanup."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from similarity import DescriptionMatcher
from stash_api import (
    GalleryItem,
    ImageItem,
    add_gallery_images,
    create_gallery,
    destroy_galleries,
    find_galleries_by_ids,
    find_images_by_ids,
    find_images_by_tag,
    find_images_in_galleries,
    find_managed_galleries,
    get_configuration,
    remove_gallery_images,
    resolve_tag_id,
    set_gallery_cover,
    update_gallery,
)
from stash_plugin import (
    PluginInput,
    Progress,
    StashClient,
    as_object,
    as_str,
    emit_info,
    emit_progress,
    emit_warn,
)

DEFAULT_SIMILARITY_THRESHOLD = 0.80
DEFAULT_RATING_STEP = 20
RATING_SYSTEM_STEPS = {"stars": 20, "decimal": 10, "percent": 1}
STAR_PRECISION_STEPS = {"full": 20, "half": 10, "quarter": 5}
EPOCH_RE = re.compile(r"^\d{10,13}$")
TITLE_DATETIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?)")
DATE_PREFIX_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class ClusterConfig:
    source_tag: str
    gallery_tag: str
    similarity_threshold: float
    rating_step: int


@dataclass(frozen=True)
class ClusterSettings:
    """Resolved values a sync pass needs."""

    gallery_tag_id: str
    source_tag_id: str
    similarity_threshold: float
    rating_step: int


def parse_threshold(value: object) -> float:
    """Read the similarity threshold, treating an empty or non-positive value as the default.

    Stash has no default for plugin settings, and `0` is never a meaningful threshold, so
    both mean "use the default" instead of failing.
    """
    if value is None or value == "":
        return DEFAULT_SIMILARITY_THRESHOLD
    try:
        parsed = float(value) if isinstance(value, int | float | str) else float(str(value))
    except ValueError as exc:
        raise ValueError(f"similarity_threshold must be a number; received: {value!r}") from exc
    if parsed <= 0:
        return DEFAULT_SIMILARITY_THRESHOLD
    if parsed > 1:
        raise ValueError(f"similarity_threshold must be in (0, 1]; received: {parsed}")
    return parsed


def parse_rating_step(ui: dict[str, object]) -> int:
    """Smallest `rating100` increment the UI can display, from the configured rating system."""
    options = as_object(ui.get("ratingSystemOptions")) or {}
    system = as_str(options.get("type")) or "stars"
    if system != "stars":
        return RATING_SYSTEM_STEPS.get(system, DEFAULT_RATING_STEP)
    precision = as_str(options.get("starPrecision")) or "full"
    return STAR_PRECISION_STEPS.get(precision, DEFAULT_RATING_STEP)


def parse_config(settings: dict[str, object], ui: dict[str, object]) -> ClusterConfig:
    return ClusterConfig(
        source_tag=(as_str(settings.get("source_tag")) or "").strip(),
        gallery_tag=(as_str(settings.get("gallery_tag")) or "").strip(),
        similarity_threshold=parse_threshold(settings.get("similarity_threshold")),
        rating_step=parse_rating_step(ui),
    )


def select_candidates(images: list[ImageItem], tag_id: str) -> list[ImageItem]:
    """Keep ungrouped images that carry the source tag and have a description to match on."""
    return [
        img for img in images if tag_id in img.tag_ids and not img.organized and bool(img.details)
    ]


def split_tagged_members(
    images: list[ImageItem], source_tag_id: str
) -> tuple[list[ImageItem], list[str]]:
    """Split members into the ones still carrying the source tag and the IDs that lost it."""
    kept: list[ImageItem] = []
    dropped: list[str] = []
    for image in images:
        if source_tag_id in image.tag_ids:
            kept.append(image)
        else:
            dropped.append(image.id)
    return kept, dropped


def image_sort_key(image: ImageItem) -> tuple[int, int, str, int]:
    """Chronological sort key: numeric epoch code -> title datetime -> date -> created_at -> id."""
    try:
        numeric_id = int(image.id)
    except ValueError:
        numeric_id = 0

    if EPOCH_RE.fullmatch(image.code):
        epoch = int(image.code)
        if len(image.code) == 10:
            epoch *= 1000
        return (0, epoch, "", numeric_id)

    if match := TITLE_DATETIME_RE.match(image.title):
        return (1, 0, match.group(1).replace("T", " "), numeric_id)

    if image.date:
        return (2, 0, image.date, numeric_id)

    if image.created_at:
        return (3, 0, image.created_at, numeric_id)

    return (4, 0, "", numeric_id)


def _extract_date(image: ImageItem) -> str:
    if image.date:
        return image.date
    if match := DATE_PREFIX_RE.match(image.title):
        return match.group(1)
    if len(image.created_at) >= 10:
        return image.created_at[:10]
    return ""


def _extract_title(image: ImageItem) -> str:
    if image.title:
        return image.title
    if image.date:
        return image.date
    if len(image.created_at) >= 16:
        return image.created_at[:16].replace("T", " ")
    return image.id


@dataclass(frozen=True)
class GalleryMetadata:
    title: str
    date: str
    details: str
    cover_image_id: str
    performer_ids: tuple[str, ...]
    studio_id: str | None
    rating100: int


def average_rating100(images: list[ImageItem], step: int) -> int:
    """Average the ratings that exist, then snap to a step the UI can display.

    Unrated images are left out instead of counting as zero, so one rated image in a
    gallery is not diluted by the ones nobody rated.
    """
    ratings = [image.rating100 for image in images if image.rating100]
    if not ratings:
        return 0
    mean = sum(ratings) / len(ratings)
    return min(int((mean + step / 2) // step) * step, 100)


def common_studio_id(images: list[ImageItem]) -> str | None:
    """The studio shared by every image, or None when they disagree."""
    studios = {image.studio_id for image in images}
    if len(studios) != 1:
        return None
    return studios.pop()


def derive_gallery_metadata(images: list[ImageItem], rating_step: int) -> GalleryMetadata:
    """Derive the gallery fields from a non-empty list of images.

    Everything but `date` follows the latest image, so a gallery presents its newest
    variant: `title`, `details`, and `cover`. `date` follows the earliest image so the
    gallery sorts by when the batch started. `performer_ids` is the union over every
    image, `studio_id` survives only when all images share it, and `rating100` is the
    average of the ratings that exist.
    """
    ordered = sorted(images, key=image_sort_key)
    earliest = ordered[0]
    latest = next((image for image in reversed(ordered) if image.details), ordered[-1])
    performers = sorted({performer_id for image in images for performer_id in image.performer_ids})
    return GalleryMetadata(
        title=_extract_title(latest),
        date=_extract_date(earliest),
        details=latest.details,
        cover_image_id=latest.id,
        performer_ids=tuple(performers),
        studio_id=common_studio_id(images),
        rating100=average_rating100(images, rating_step),
    )


@dataclass
class _ClusterBucket:
    gallery: GalleryItem
    images: list[ImageItem]
    is_new: bool = False


def _sync_gallery_fields(
    client: StashClient,
    bucket: _ClusterBucket,
    settings: ClusterSettings,
    *,
    dry_run: bool,
) -> bool:
    """Update derived gallery fields if out of sync. Returns whether anything changed."""
    if bucket.gallery.organized or not bucket.images:
        return False

    metadata = derive_gallery_metadata(bucket.images, settings.rating_step)

    update_title = metadata.title if bucket.gallery.title != metadata.title else None
    update_date = metadata.date if metadata.date and bucket.gallery.date != metadata.date else None
    update_details = (
        metadata.details
        if metadata.details and bucket.gallery.details != metadata.details
        else None
    )
    need_cover = bucket.gallery.cover_id != metadata.cover_image_id
    need_performers = set(bucket.gallery.performer_ids) != set(metadata.performer_ids)
    need_studio = bucket.gallery.studio_id != metadata.studio_id
    need_rating = (bucket.gallery.rating100 or 0) != metadata.rating100

    if (
        update_title is None
        and update_date is None
        and update_details is None
        and not need_cover
        and not need_performers
        and not need_studio
        and not need_rating
    ):
        return False

    if not dry_run:
        if (
            update_title is not None
            or update_date is not None
            or update_details is not None
            or need_performers
            or need_studio
            or need_rating
        ):
            update_gallery(
                client,
                gallery_id=bucket.gallery.id,
                title=update_title,
                date=update_date,
                details=update_details,
                performer_ids=metadata.performer_ids if need_performers else None,
                studio_id=metadata.studio_id if need_studio else None,
                clear_studio=need_studio and metadata.studio_id is None,
                rating100=metadata.rating100 if need_rating else None,
            )
        if need_cover:
            set_gallery_cover(client, bucket.gallery.id, metadata.cover_image_id)

    if update_title is not None:
        bucket.gallery.title = update_title
    if update_date is not None:
        bucket.gallery.date = update_date
    if update_details is not None:
        bucket.gallery.details = update_details
    if need_cover:
        bucket.gallery.cover_id = metadata.cover_image_id
    if need_performers:
        bucket.gallery.performer_ids = metadata.performer_ids
    if need_studio:
        bucket.gallery.studio_id = metadata.studio_id
    if need_rating:
        bucket.gallery.rating100 = metadata.rating100
    return True


def _cluster_groups(
    candidates: list[ImageItem],
    settings: ClusterSettings,
    *,
    report_progress: bool = False,
) -> list[list[ImageItem]]:
    """Group every candidate from scratch, ignoring how the images are grouped today."""
    matcher = DescriptionMatcher(settings.similarity_threshold)
    ordered = sorted(candidates, key=image_sort_key)
    groups: list[list[ImageItem]] = []
    progress = (
        Progress(len(ordered), label="Gallery Cluster: clustering images")
        if report_progress
        else None
    )
    for img in ordered:
        match = matcher.find_best(img.details)
        if match is None:
            matcher.add(str(len(groups)), img.details)
            groups.append([img])
        else:
            groups[int(match[0])].append(img)
        if progress:
            progress.step()
    return groups


def _apply_groups(
    client: StashClient,
    groups: list[list[ImageItem]],
    managed_galleries: list[GalleryItem],
    settings: ClusterSettings,
    *,
    dry_run: bool,
    report_progress: bool = False,
) -> dict[str, int]:
    """Write the groups to Stash, reusing a managed gallery per group when one fits.

    A group takes over the gallery that already holds most of its images, so regrouping does
    not churn gallery IDs. Galleries no group wants are deleted, groups without one are
    created.
    """
    editable = [gallery for gallery in managed_galleries if not gallery.organized]
    members_by_gallery: dict[str, set[str]] = {gallery.id: set() for gallery in editable}
    galleries_by_image: dict[str, set[str]] = {}
    for img in find_images_in_galleries(client, [gallery.id for gallery in editable]):
        for gallery_id in img.gallery_ids:
            if gallery_id in members_by_gallery:
                members_by_gallery[gallery_id].add(img.id)
                galleries_by_image.setdefault(img.id, set()).add(gallery_id)

    taken: set[str] = set()
    targets: list[str | None] = []
    for group in groups:
        counts = Counter(
            gallery_id for img in group for gallery_id in galleries_by_image.get(img.id, ())
        )
        target = next((gid for gid, _count in counts.most_common() if gid not in taken), None)
        if target is not None:
            taken.add(target)
        targets.append(target)

    gallery_by_id = {gallery.id: gallery for gallery in editable}
    progress = (
        Progress(len(groups), label="Gallery Cluster: writing galleries")
        if report_progress
        else None
    )
    created = updated = removed = 0
    for group, target in zip(groups, targets, strict=True):
        if target is None:
            metadata = derive_gallery_metadata(group, settings.rating_step)
            if not dry_run:
                gallery_id = create_gallery(
                    client,
                    title=metadata.title,
                    date=metadata.date,
                    details=metadata.details,
                    tag_id=settings.gallery_tag_id,
                    performer_ids=metadata.performer_ids,
                    studio_id=metadata.studio_id,
                    rating100=metadata.rating100,
                )
                add_gallery_images(client, gallery_id, [img.id for img in group])
            created += 1
        else:
            desired = {img.id for img in group}
            current = members_by_gallery[target]
            to_add = sorted(desired - current)
            to_remove = sorted(current - desired)
            if to_remove:
                removed += len(to_remove)
                if not dry_run:
                    remove_gallery_images(client, target, to_remove)
            if to_add and not dry_run:
                add_gallery_images(client, target, to_add)
            bucket = _ClusterBucket(gallery=gallery_by_id[target], images=group)
            changed = _sync_gallery_fields(client, bucket, settings, dry_run=dry_run)
            if to_add or to_remove or changed:
                updated += 1
        if progress:
            progress.step()

    obsolete = [gallery_id for gallery_id in members_by_gallery if gallery_id not in taken]
    if obsolete and not dry_run:
        destroy_galleries(client, obsolete)
    return {
        "created": created,
        "reused": len(taken),
        "updated": updated,
        "removed": removed,
        "destroyed": len(obsolete),
    }


def _cleanup_empty_galleries(
    client: StashClient,
    galleries: list[GalleryItem],
    *,
    dry_run: bool,
) -> int:
    empty_ids = [g.id for g in galleries if g.image_count == 0 and not g.organized]
    if empty_ids and not dry_run:
        destroy_galleries(client, empty_ids)
    return len(empty_ids)


def _assign_images_to_clusters(
    client: StashClient,
    candidates: list[ImageItem],
    managed_galleries: list[GalleryItem],
    settings: ClusterSettings,
    *,
    dry_run: bool,
    report_progress: bool = False,
) -> dict[str, int]:
    """Match ungrouped candidate images to existing galleries or create new ones."""
    managed_ids = {g.id for g in managed_galleries}
    to_process = sorted(
        (img for img in candidates if not any(gid in managed_ids for gid in img.gallery_ids)),
        key=image_sort_key,
    )
    if not to_process:
        return {"evaluated": 0, "joined": 0, "created": 0, "updated": 0}

    buckets: dict[str, _ClusterBucket] = {}
    matcher = DescriptionMatcher(settings.similarity_threshold)

    active_gallery_ids = [g.id for g in managed_galleries if g.image_count > 0]
    images_by_gallery: dict[str, list[ImageItem]] = {gid: [] for gid in active_gallery_ids}
    if active_gallery_ids:
        for img in find_images_in_galleries(client, active_gallery_ids):
            # Members that lost the source tag are dropped by the sync pass, so leave them
            # out of the metadata of galleries this pass rewrites.
            if settings.source_tag_id not in img.tag_ids:
                continue
            for gid in img.gallery_ids:
                if gid in images_by_gallery:
                    images_by_gallery[gid].append(img)

    for gallery in managed_galleries:
        if gallery.image_count <= 0:
            continue
        bucket = _ClusterBucket(gallery=gallery, images=images_by_gallery.get(gallery.id, []))
        buckets[gallery.id] = bucket
        if not gallery.organized and gallery.details:
            matcher.add(gallery.id, gallery.details)

    progress = (
        Progress(len(to_process), label="Gallery Cluster: clustering images")
        if report_progress
        else None
    )
    joined = 0
    created = 0
    pending_adds: dict[str, list[str]] = {}
    touched_buckets: set[str] = set()
    synthetic_counter = 0

    for img in to_process:
        match = matcher.find_best(img.details)
        if match is not None:
            target_id, _score = match
            bucket = buckets[target_id]
            bucket.images.append(img)
            bucket.gallery.image_count += 1
            pending_adds.setdefault(target_id, []).append(img.id)
            touched_buckets.add(target_id)
            joined += 1
        else:
            metadata = derive_gallery_metadata([img], settings.rating_step)
            if dry_run:
                synthetic_counter += 1
                new_id = f"dry-run-{synthetic_counter}"
            else:
                new_id = create_gallery(
                    client,
                    title=metadata.title,
                    date=metadata.date,
                    details=metadata.details,
                    tag_id=settings.gallery_tag_id,
                    performer_ids=metadata.performer_ids,
                    studio_id=metadata.studio_id,
                    rating100=metadata.rating100,
                )
                # Stash sets the cover of a gallery without one to the first added image, so
                # nothing else is needed here.
                add_gallery_images(client, new_id, [img.id])

            new_gallery = GalleryItem(
                id=new_id,
                title=metadata.title,
                date=metadata.date,
                details=metadata.details,
                organized=False,
                image_count=1,
                rating100=metadata.rating100,
                studio_id=metadata.studio_id,
                cover_id=metadata.cover_image_id,
                tag_ids=(settings.gallery_tag_id,),
                performer_ids=metadata.performer_ids,
            )
            buckets[new_id] = _ClusterBucket(gallery=new_gallery, images=[img], is_new=True)
            matcher.add(new_id, img.details)
            created += 1

        if progress:
            progress.step()

    if not dry_run:
        for gallery_id, image_ids in pending_adds.items():
            add_gallery_images(client, gallery_id, image_ids)

    updated = 0
    for gallery_id in touched_buckets:
        if _sync_gallery_fields(client, buckets[gallery_id], settings, dry_run=dry_run):
            updated += 1

    return {
        "evaluated": len(to_process),
        "joined": joined,
        "created": created,
        "updated": updated,
    }


def _sync_all_managed_galleries(
    client: StashClient,
    managed_galleries: list[GalleryItem],
    settings: ClusterSettings,
    *,
    dry_run: bool,
    report_progress: bool = False,
) -> tuple[int, int, int]:
    """Enforce membership by the source tag, then refresh metadata.

    Returns `(updated, removed, destroyed)`.
    """
    destroyed = _cleanup_empty_galleries(client, managed_galleries, dry_run=dry_run)
    non_empty = [g for g in managed_galleries if g.image_count > 0 and not g.organized]
    if not non_empty:
        return 0, 0, destroyed

    images_by_gallery: dict[str, list[ImageItem]] = {g.id: [] for g in non_empty}
    for img in find_images_in_galleries(client, [g.id for g in non_empty]):
        for gid in img.gallery_ids:
            if gid in images_by_gallery:
                images_by_gallery[gid].append(img)

    progress = (
        Progress(len(non_empty), label="Gallery Cluster: syncing galleries")
        if report_progress
        else None
    )
    updated = 0
    removed = 0
    for gallery in non_empty:
        members, untagged = split_tagged_members(
            images_by_gallery.get(gallery.id, []), settings.source_tag_id
        )
        if untagged:
            removed += len(untagged)
            if not dry_run:
                remove_gallery_images(client, gallery.id, untagged)
        if not members:
            if not dry_run:
                destroy_galleries(client, [gallery.id])
            destroyed += 1
        else:
            bucket = _ClusterBucket(gallery=gallery, images=members)
            if _sync_gallery_fields(client, bucket, settings, dry_run=dry_run) or untagged:
                updated += 1
        if progress:
            progress.step()
    return updated, removed, destroyed


def run_cluster(
    plugin_input: PluginInput,
    *,
    dry_run: bool,
    entity_type: str | None,
    item_ids: list[str] | None,
) -> object:
    client = StashClient.connect(plugin_input)
    configuration = get_configuration(client)
    config = parse_config(configuration.settings, configuration.ui)

    if not config.source_tag or not config.gallery_tag:
        message = "Skipped: configure both 'Source tag' and 'Gallery tag' in Plugin Settings first."
        if entity_type is None:
            emit_warn(message)
        else:
            emit_info(message)
        return {"dry_run": dry_run, "skipped_unconfigured": True}

    source_tag_id = resolve_tag_id(client, config.source_tag, create_missing=False)
    if source_tag_id is None:
        message = f"Skipped: source tag {config.source_tag!r} does not exist."
        if entity_type is None:
            emit_warn(message)
        else:
            emit_info(message)
        return {"dry_run": dry_run, "missing_source_tag": config.source_tag}

    tag_id = resolve_tag_id(client, config.gallery_tag, create_missing=not dry_run)
    if tag_id is None:
        message = f"Skipped: gallery tag {config.gallery_tag!r} does not exist yet."
        if entity_type is None:
            emit_warn(message)
        else:
            emit_info(message)
        return {"dry_run": dry_run, "missing_tag": config.gallery_tag}

    cluster_settings = ClusterSettings(
        gallery_tag_id=tag_id,
        source_tag_id=source_tag_id,
        similarity_threshold=config.similarity_threshold,
        rating_step=config.rating_step,
    )

    if entity_type == "image" and item_ids is not None:
        images = find_images_by_ids(client, item_ids)
        managed = find_managed_galleries(client, tag_id)

        # An image that already belongs to a managed gallery changes that gallery, and an
        # image that lost the source tag has to leave it, so resync those galleries even
        # when the image is not a clustering candidate.
        affected = [g for g in managed if any(g.id in img.gallery_ids for img in images)]
        resynced, removed, destroyed = (
            _sync_all_managed_galleries(client, affected, cluster_settings, dry_run=dry_run)
            if affected
            else (0, 0, 0)
        )

        candidates = select_candidates(images, source_tag_id)
        stats = (
            _assign_images_to_clusters(
                client,
                candidates,
                managed,
                cluster_settings,
                dry_run=dry_run,
            )
            if candidates
            else {"evaluated": 0, "joined": 0, "created": 0, "updated": 0}
        )

        emit_info(
            f"Gallery Cluster: evaluated {stats['evaluated']} images, "
            f"joined {stats['joined']} into existing galleries, "
            f"created {stats['created']} new galleries, "
            f"updated {stats['updated'] + resynced} galleries, "
            f"removed {removed} untagged images, destroyed {destroyed} empty galleries."
        )
        return {
            "dry_run": dry_run,
            **stats,
            "updated": stats["updated"] + resynced,
            "removed": removed,
            "destroyed": destroyed,
        }

    if entity_type == "gallery" and item_ids is not None:
        galleries = [g for g in find_galleries_by_ids(client, item_ids) if tag_id in g.tag_ids]
        if not galleries:
            return {"dry_run": dry_run, "updated": 0, "removed": 0, "destroyed": 0}
        updated, removed, destroyed = _sync_all_managed_galleries(
            client, galleries, cluster_settings, dry_run=dry_run
        )
        emit_info(
            f"Gallery Cluster: updated {updated} galleries, removed {removed} untagged images, "
            f"destroyed {destroyed} empty galleries."
        )
        return {"dry_run": dry_run, "updated": updated, "removed": removed, "destroyed": destroyed}

    if entity_type == "image_destroy":
        managed = find_managed_galleries(client, tag_id)
        updated, removed, destroyed = _sync_all_managed_galleries(
            client, managed, cluster_settings, dry_run=dry_run
        )
        emit_info(
            f"Gallery Cluster: updated {updated} galleries, removed {removed} untagged images, "
            f"destroyed {destroyed} empty galleries."
        )
        return {"dry_run": dry_run, "updated": updated, "removed": removed, "destroyed": destroyed}

    # Full Rebuild / Dry Run task
    emit_info("Gallery Cluster: loading tagged images and managed galleries...")
    candidates = select_candidates(find_images_by_tag(client, source_tag_id), source_tag_id)
    managed = find_managed_galleries(client, tag_id)

    # Images inside an organized gallery are the manual lock: leave them and their gallery alone.
    locked_ids = {gallery.id for gallery in managed if gallery.organized}
    candidates = [img for img in candidates if not locked_ids.intersection(img.gallery_ids)]
    matched_images = len(candidates)

    emit_info(f"Gallery Cluster: clustering {matched_images} tagged images from scratch...")
    groups = _cluster_groups(candidates, cluster_settings, report_progress=True)

    emit_info(f"Gallery Cluster: writing {len(groups)} galleries...")
    apply_stats = _apply_groups(
        client,
        groups,
        managed,
        cluster_settings,
        dry_run=dry_run,
        report_progress=True,
    )

    emit_progress(1.0)
    summary = {
        "dry_run": dry_run,
        "matched_images": matched_images,
        "groups": len(groups),
        **apply_stats,
    }
    mode = "dry run" if dry_run else "rebuild"
    emit_info(
        f"Gallery Cluster {mode}: matched {summary['matched_images']} tagged images, "
        f"grouped them into {summary['groups']} galleries, "
        f"created {summary['created']} new galleries, reused {summary['reused']}, "
        f"updated {summary['updated']}, removed {summary['removed']} images from galleries, "
        f"destroyed {summary['destroyed']} obsolete galleries."
    )
    return summary
