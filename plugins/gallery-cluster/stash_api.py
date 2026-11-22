"""GraphQL operations and typed helpers for the Gallery Cluster plugin."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import NotRequired, TypedDict

from stash_plugin import Operation, StashClient, as_object

PLUGIN_ID = "gallery-cluster"
IMAGE_QUERY_CHUNK = 500


class _TagRef(TypedDict):
    id: str
    name: NotRequired[str]


class _CoverRef(TypedDict):
    id: str


class _GalleryRef(TypedDict):
    id: str


class _PerformerRef(TypedDict):
    id: str


class _StudioRef(TypedDict):
    id: str


class _VisualFile(TypedDict):
    path: NotRequired[str]


class RawImage(TypedDict):
    id: str
    title: str | None
    code: str | None
    date: str | None
    details: str | None
    organized: bool
    created_at: str
    rating100: int | None
    visual_files: list[_VisualFile]
    galleries: list[_GalleryRef]
    tags: list[_TagRef]
    performers: list[_PerformerRef]
    studio: _StudioRef | None


class RawGallery(TypedDict):
    id: str
    title: str | None
    date: str | None
    details: str | None
    organized: bool
    image_count: int
    rating100: int | None
    cover: _CoverRef | None
    tags: list[_TagRef]
    performers: list[_PerformerRef]
    studio: _StudioRef | None


class _ImagesPage(TypedDict):
    images: list[RawImage]


class _FindImagesData(TypedDict):
    findImages: _ImagesPage


class _GalleriesPage(TypedDict):
    galleries: list[RawGallery]


class _FindGalleriesData(TypedDict):
    findGalleries: _GalleriesPage


class _TagsPage(TypedDict):
    tags: list[_TagRef]


class _FindTagsData(TypedDict):
    findTags: _TagsPage


class _FindTagData(TypedDict):
    findTag: _TagRef | None


class _TagCreateData(TypedDict):
    tagCreate: _TagRef | None


class _GalleryCreateData(TypedDict):
    galleryCreate: _GalleryRef | None


class _GalleryUpdateData(TypedDict):
    galleryUpdate: _GalleryRef | None


class _Configuration(TypedDict):
    plugins: object
    ui: object


class _PluginConfigData(TypedDict):
    configuration: _Configuration


PLUGIN_SETTINGS = Operation[_PluginConfigData]("""
    query GalleryClusterSettings($pluginIds: [ID!]) {
      configuration {
        plugins(include: $pluginIds)
        ui
      }
    }
""")

FIND_TAG_BY_ID = Operation[_FindTagData]("""
    query GalleryClusterFindTag($id: ID!) {
      findTag(id: $id) {
        id
        name
      }
    }
""")

FIND_TAGS_BY_NAME = Operation[_FindTagsData]("""
    query GalleryClusterFindTags($tagFilter: TagFilterType) {
      findTags(tag_filter: $tagFilter, filter: { per_page: -1 }) {
        tags {
          id
          name
        }
      }
    }
""")

TAG_CREATE = Operation[_TagCreateData]("""
    mutation GalleryClusterTagCreate($input: TagCreateInput!) {
      tagCreate(input: $input) {
        id
        name
      }
    }
""")

FIND_IMAGES = Operation[_FindImagesData]("""
    query GalleryClusterFindImages(
      $imageFilter: ImageFilterType,
      $filter: FindFilterType,
      $ids: [ID!]
    ) {
      findImages(image_filter: $imageFilter, filter: $filter, ids: $ids) {
        images {
          id
          title
          code
          date
          details
          organized
          created_at
          rating100
          visual_files {
            ... on ImageFile {
              path
            }
          }
          galleries {
            id
          }
          tags {
            id
          }
          performers {
            id
          }
          studio {
            id
          }
        }
      }
    }
""")

FIND_GALLERY_ANCHORS = Operation[_FindGalleriesData]("""
    query GalleryClusterGalleryAnchors(
      $galleryFilter: GalleryFilterType,
      $filter: FindFilterType
    ) {
      findGalleries(gallery_filter: $galleryFilter, filter: $filter) {
        galleries {
          id
          details
          organized
          image_count
        }
      }
    }
""")

FIND_GALLERIES = Operation[_FindGalleriesData]("""
    query GalleryClusterFindGalleries(
      $galleryFilter: GalleryFilterType,
      $filter: FindFilterType,
      $ids: [ID!]
    ) {
      findGalleries(gallery_filter: $galleryFilter, filter: $filter, ids: $ids) {
        galleries {
          id
          title
          date
          details
          organized
          image_count
          rating100
          cover {
            id
          }
          tags {
            id
          }
          performers {
            id
          }
          studio {
            id
          }
        }
      }
    }
""")

GALLERY_CREATE = Operation[_GalleryCreateData]("""
    mutation GalleryClusterCreate($input: GalleryCreateInput!) {
      galleryCreate(input: $input) {
        id
      }
    }
""")

GALLERY_UPDATE = Operation[_GalleryUpdateData]("""
    mutation GalleryClusterUpdate($input: GalleryUpdateInput!) {
      galleryUpdate(input: $input) {
        id
      }
    }
""")

GALLERY_DESTROY = Operation[object]("""
    mutation GalleryClusterDestroy($input: GalleryDestroyInput!) {
      galleryDestroy(input: $input)
    }
""")

ADD_GALLERY_IMAGES = Operation[object]("""
    mutation GalleryClusterAddImages($input: GalleryAddInput!) {
      addGalleryImages(input: $input)
    }
""")

REMOVE_GALLERY_IMAGES = Operation[object]("""
    mutation GalleryClusterRemoveImages($input: GalleryRemoveInput!) {
      removeGalleryImages(input: $input)
    }
""")

SET_GALLERY_COVER = Operation[object]("""
    mutation GalleryClusterSetCover($input: GallerySetCoverInput!) {
      setGalleryCover(input: $input)
    }
""")


@dataclass(frozen=True)
class ImageItem:
    id: str
    title: str
    code: str
    date: str
    details: str
    organized: bool
    created_at: str
    rating100: int | None
    studio_id: str | None
    paths: tuple[str, ...]
    gallery_ids: tuple[str, ...]
    tag_ids: tuple[str, ...]
    performer_ids: tuple[str, ...]


@dataclass
class GalleryItem:
    id: str
    title: str
    date: str
    details: str
    organized: bool
    image_count: int
    rating100: int | None
    studio_id: str | None
    cover_id: str | None
    tag_ids: tuple[str, ...]
    performer_ids: tuple[str, ...]


@dataclass(frozen=True)
class GalleryAnchor:
    """A managed gallery as far as matching and empty cleanup need it."""

    id: str
    details: str
    organized: bool
    image_count: int


@dataclass(frozen=True)
class PluginConfiguration:
    """Plugin settings plus the UI configuration, which carries the rating system."""

    settings: dict[str, object]
    ui: dict[str, object]


def _to_image_item(raw: RawImage) -> ImageItem:
    paths = tuple(path for vf in raw["visual_files"] if (path := vf.get("path")))
    return ImageItem(
        id=raw["id"],
        title=(raw["title"] or "").strip(),
        code=(raw["code"] or "").strip(),
        date=(raw["date"] or "").strip(),
        details=(raw["details"] or "").strip(),
        organized=raw["organized"],
        created_at=raw["created_at"],
        rating100=raw["rating100"],
        studio_id=raw["studio"]["id"] if raw["studio"] else None,
        paths=paths,
        gallery_ids=tuple(g["id"] for g in raw["galleries"]),
        tag_ids=tuple(t["id"] for t in raw["tags"]),
        performer_ids=tuple(p["id"] for p in raw["performers"]),
    )


def _to_gallery_item(raw: RawGallery) -> GalleryItem:
    return GalleryItem(
        id=raw["id"],
        title=(raw["title"] or "").strip(),
        date=(raw["date"] or "").strip(),
        details=(raw["details"] or "").strip(),
        organized=raw["organized"],
        image_count=raw["image_count"],
        rating100=raw["rating100"],
        studio_id=raw["studio"]["id"] if raw["studio"] else None,
        cover_id=raw["cover"]["id"] if raw["cover"] else None,
        tag_ids=tuple(t["id"] for t in raw["tags"]),
        performer_ids=tuple(p["id"] for p in raw["performers"]),
    )


def get_configuration(client: StashClient) -> PluginConfiguration:
    data = client.execute(PLUGIN_SETTINGS, {"pluginIds": [PLUGIN_ID]})
    configuration = data["configuration"]
    plugins = as_object(configuration["plugins"]) or {}
    return PluginConfiguration(
        settings=as_object(plugins.get(PLUGIN_ID)) or {},
        ui=as_object(configuration["ui"]) or {},
    )


def resolve_tag_id(client: StashClient, tag_ref: str, *, create_missing: bool) -> str | None:
    cleaned = tag_ref.strip()
    if not cleaned:
        return None
    if cleaned.isdigit():
        found = client.execute(FIND_TAG_BY_ID, {"id": cleaned})["findTag"]
        return found["id"] if found else None

    found_list = client.execute(
        FIND_TAGS_BY_NAME,
        {"tagFilter": {"name": {"value": cleaned, "modifier": "EQUALS"}}},
    )["findTags"]["tags"]
    if found_list:
        return found_list[0]["id"]
    if not create_missing:
        return None
    created = client.execute(TAG_CREATE, {"input": {"name": cleaned}})["tagCreate"]
    if not created:
        raise RuntimeError(f"Failed to create gallery tag: {cleaned}")
    return created["id"]


def chunks(values: list[str], size: int = IMAGE_QUERY_CHUNK) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def find_images_by_ids(client: StashClient, image_ids: list[str]) -> list[ImageItem]:
    found: dict[str, ImageItem] = {}
    for chunk in chunks(image_ids):
        data = client.execute(FIND_IMAGES, {"ids": chunk, "filter": {"per_page": -1}})
        for raw in data["findImages"]["images"]:
            item = _to_image_item(raw)
            found[item.id] = item
    return list(found.values())


def find_images_by_tag(client: StashClient, tag_id: str) -> list[ImageItem]:
    data = client.execute(
        FIND_IMAGES,
        {
            "imageFilter": {"tags": {"value": [tag_id], "modifier": "INCLUDES"}},
            "filter": {"per_page": -1},
        },
    )
    return [_to_image_item(raw) for raw in data["findImages"]["images"]]


def find_images_in_galleries(client: StashClient, gallery_ids: list[str]) -> list[ImageItem]:
    """Members of every given gallery.

    Chunked because a rebuild passes thousands of gallery IDs, and one query with all of
    them keeps Stash busy for minutes.
    """
    found: dict[str, ImageItem] = {}
    for chunk in chunks(gallery_ids):
        data = client.execute(
            FIND_IMAGES,
            {
                "imageFilter": {"galleries": {"value": chunk, "modifier": "INCLUDES"}},
                "filter": {"per_page": -1},
            },
        )
        for raw in data["findImages"]["images"]:
            item = _to_image_item(raw)
            found[item.id] = item
    return list(found.values())


def find_gallery_anchors(client: StashClient, tag_id: str) -> list[GalleryAnchor]:
    data = client.execute(
        FIND_GALLERY_ANCHORS,
        {
            "galleryFilter": {"tags": {"value": [tag_id], "modifier": "INCLUDES"}},
            "filter": {"per_page": -1},
        },
    )
    return [
        GalleryAnchor(
            id=raw["id"],
            details=(raw["details"] or "").strip(),
            organized=raw["organized"],
            image_count=raw["image_count"],
        )
        for raw in data["findGalleries"]["galleries"]
    ]


def find_managed_galleries(client: StashClient, tag_id: str) -> list[GalleryItem]:
    data = client.execute(
        FIND_GALLERIES,
        {
            "galleryFilter": {"tags": {"value": [tag_id], "modifier": "INCLUDES"}},
            "filter": {"per_page": -1},
        },
    )
    return [_to_gallery_item(raw) for raw in data["findGalleries"]["galleries"]]


def find_galleries_by_ids(client: StashClient, gallery_ids: list[str]) -> list[GalleryItem]:
    if not gallery_ids:
        return []
    data = client.execute(FIND_GALLERIES, {"ids": gallery_ids, "filter": {"per_page": -1}})
    return [_to_gallery_item(raw) for raw in data["findGalleries"]["galleries"]]


def create_gallery(
    client: StashClient,
    *,
    title: str,
    date: str,
    details: str,
    tag_id: str,
    performer_ids: tuple[str, ...],
    studio_id: str | None,
    rating100: int,
) -> str:
    payload: dict[str, object] = {
        "title": title,
        "details": details,
        "tag_ids": [tag_id],
        "performer_ids": list(performer_ids),
        "rating100": rating100,
    }
    if date:
        payload["date"] = date
    if studio_id is not None:
        payload["studio_id"] = studio_id
    created = client.execute(GALLERY_CREATE, {"input": payload})["galleryCreate"]
    if not created:
        raise RuntimeError(f"Failed to create gallery: {title}")
    return created["id"]


def update_gallery(
    client: StashClient,
    *,
    gallery_id: str,
    title: str | None = None,
    date: str | None = None,
    details: str | None = None,
    performer_ids: tuple[str, ...] | None = None,
    studio_id: str | None = None,
    clear_studio: bool = False,
    rating100: int | None = None,
) -> None:
    """Update the given fields; `clear_studio` sends an explicit null, which Stash accepts."""
    payload: dict[str, object] = {"id": gallery_id}
    if title is not None:
        payload["title"] = title
    if date is not None:
        payload["date"] = date
    if details is not None:
        payload["details"] = details
    if performer_ids is not None:
        payload["performer_ids"] = list(performer_ids)
    if studio_id is not None:
        payload["studio_id"] = studio_id
    elif clear_studio:
        payload["studio_id"] = None
    if rating100 is not None:
        payload["rating100"] = rating100
    _ = client.execute(GALLERY_UPDATE, {"input": payload})


def add_gallery_images(client: StashClient, gallery_id: str, image_ids: list[str]) -> None:
    if not image_ids:
        return
    _ = client.execute(
        ADD_GALLERY_IMAGES,
        {"input": {"gallery_id": gallery_id, "image_ids": image_ids}},
    )


def remove_gallery_images(client: StashClient, gallery_id: str, image_ids: list[str]) -> None:
    if not image_ids:
        return
    _ = client.execute(
        REMOVE_GALLERY_IMAGES,
        {"input": {"gallery_id": gallery_id, "image_ids": image_ids}},
    )


def set_gallery_cover(client: StashClient, gallery_id: str, cover_image_id: str) -> None:
    _ = client.execute(
        SET_GALLERY_COVER,
        {"input": {"gallery_id": gallery_id, "cover_image_id": cover_image_id}},
    )


def destroy_galleries(client: StashClient, gallery_ids: list[str]) -> None:
    if not gallery_ids:
        return
    _ = client.execute(
        GALLERY_DESTROY,
        {
            "input": {
                "ids": gallery_ids,
                "delete_file": False,
                "delete_generated": False,
                "destroy_file_entry": False,
            }
        },
    )
