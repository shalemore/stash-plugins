"""GraphQL operations and typed helpers for the Gallery Cluster plugin."""

from dataclasses import dataclass
from typing import NotRequired, TypedDict

from stash_plugin import Operation, StashClient, as_object

PLUGIN_ID = "gallery-cluster"


class _TagRef(TypedDict):
    id: str
    name: NotRequired[str]


class _CoverRef(TypedDict):
    id: str


class _GalleryRef(TypedDict):
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
    visual_files: list[_VisualFile]
    galleries: list[_GalleryRef]


class RawGallery(TypedDict):
    id: str
    title: str | None
    date: str | None
    details: str | None
    organized: bool
    image_count: int
    cover: _CoverRef | None
    tags: list[_TagRef]


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


class _PluginConfigData(TypedDict):
    configuration: _Configuration


PLUGIN_SETTINGS = Operation[_PluginConfigData]("""
    query GalleryClusterSettings($pluginIds: [ID!]) {
      configuration {
        plugins(include: $pluginIds)
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
          visual_files {
            ... on ImageFile {
              path
            }
          }
          galleries {
            id
          }
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
          cover {
            id
          }
          tags {
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
    paths: tuple[str, ...]
    gallery_ids: tuple[str, ...]


@dataclass
class GalleryItem:
    id: str
    title: str
    date: str
    details: str
    organized: bool
    image_count: int
    cover_id: str | None
    tag_ids: tuple[str, ...]


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
        paths=paths,
        gallery_ids=tuple(g["id"] for g in raw["galleries"]),
    )


def _to_gallery_item(raw: RawGallery) -> GalleryItem:
    return GalleryItem(
        id=raw["id"],
        title=(raw["title"] or "").strip(),
        date=(raw["date"] or "").strip(),
        details=(raw["details"] or "").strip(),
        organized=raw["organized"],
        image_count=raw["image_count"],
        cover_id=raw["cover"]["id"] if raw["cover"] else None,
        tag_ids=tuple(t["id"] for t in raw["tags"]),
    )


def get_plugin_settings(client: StashClient) -> dict[str, object]:
    data = client.execute(PLUGIN_SETTINGS, {"pluginIds": [PLUGIN_ID]})
    plugins = as_object(data["configuration"]["plugins"]) or {}
    return as_object(plugins.get(PLUGIN_ID)) or {}


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


def find_images_by_ids(client: StashClient, image_ids: list[str]) -> list[ImageItem]:
    if not image_ids:
        return []
    data = client.execute(FIND_IMAGES, {"ids": image_ids, "filter": {"per_page": -1}})
    return [_to_image_item(raw) for raw in data["findImages"]["images"]]


def find_images_by_prefix(client: StashClient, prefix: str) -> list[ImageItem]:
    data = client.execute(
        FIND_IMAGES,
        {
            "imageFilter": {"path": {"value": prefix, "modifier": "INCLUDES"}},
            "filter": {"per_page": -1},
        },
    )
    return [_to_image_item(raw) for raw in data["findImages"]["images"]]


def find_images_in_galleries(client: StashClient, gallery_ids: list[str]) -> list[ImageItem]:
    if not gallery_ids:
        return []
    data = client.execute(
        FIND_IMAGES,
        {
            "imageFilter": {"galleries": {"value": gallery_ids, "modifier": "INCLUDES"}},
            "filter": {"per_page": -1},
        },
    )
    return [_to_image_item(raw) for raw in data["findImages"]["images"]]


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
) -> str:
    payload: dict[str, object] = {
        "title": title,
        "details": details,
        "tag_ids": [tag_id],
    }
    if date:
        payload["date"] = date
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
) -> None:
    payload: dict[str, object] = {"id": gallery_id}
    if title is not None:
        payload["title"] = title
    if date is not None:
        payload["date"] = date
    if details is not None:
        payload["details"] = details
    _ = client.execute(GALLERY_UPDATE, {"input": payload})


def add_gallery_images(client: StashClient, gallery_id: str, image_ids: list[str]) -> None:
    if not image_ids:
        return
    _ = client.execute(
        ADD_GALLERY_IMAGES,
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
