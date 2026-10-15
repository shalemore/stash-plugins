from typing import NotRequired, TypedDict

from stash_plugin import Operation, StashClient


class TagTree(TypedDict):
    id: str
    name: str
    aliases: list[str]
    # Absent on the deepest level of the queried tree.
    children: NotRequired[list["TagTree"]]


class _Tags(TypedDict):
    tags: list[TagTree]


class _FindTags(TypedDict):
    findTags: _Tags


class _TagCreate(TypedDict):
    tagCreate: TagTree | None


class _Folder(TypedDict):
    path: str


class _TagRef(TypedDict):
    id: str


class Gallery(TypedDict):
    id: str
    organized: bool
    folder: _Folder | None
    tags: list[_TagRef]


class _Galleries(TypedDict):
    galleries: list[Gallery]


class _FindGalleries(TypedDict):
    findGalleries: _Galleries


class _FindGallery(TypedDict):
    findGallery: Gallery | None


class UpdatedGallery(TypedDict):
    id: str
    title: str | None
    organized: bool


class _GalleryUpdate(TypedDict):
    galleryUpdate: UpdatedGallery | None


GALLERY_FIELDS = "id organized folder { path } tags { id }"

SEARCH_TAGS = Operation[_FindTags]("""
    query FindTags($filter: FindFilterType) {
      findTags(filter: $filter) {
        tags { id name aliases children { id name aliases children { id name aliases } } }
      }
    }
""")

TAG_CREATE = Operation[_TagCreate]("""
    mutation TagCreate($input: TagCreateInput!) {
      tagCreate(input: $input) { id name aliases }
    }
""")

GALLERY_UPDATE = Operation[_GalleryUpdate]("""
    mutation GalleryUpdate($input: GalleryUpdateInput!) {
      galleryUpdate(input: $input) { id title organized }
    }
""")

FIND_GALLERIES = Operation[_FindGalleries](f"""
    query FindGalleries($galleryFilter: GalleryFilterType, $filter: FindFilterType) {{
      findGalleries(gallery_filter: $galleryFilter, filter: $filter) {{
        galleries {{ {GALLERY_FIELDS} }}
      }}
    }}
""")

FIND_GALLERY = Operation[_FindGallery](f"""
    query FindGallery($id: ID!) {{
      findGallery(id: $id) {{ {GALLERY_FIELDS} }}
    }}
""")


def search_tags(client: StashClient, query: str) -> list[TagTree]:
    data = client.execute(SEARCH_TAGS, {"filter": {"q": query, "per_page": -1}})
    return data["findTags"]["tags"]


def create_tag(client: StashClient, name: str, parent_ids: list[str]) -> TagTree:
    data = client.execute(
        TAG_CREATE, {"input": {"name": name, "aliases": [name], "parent_ids": parent_ids}}
    )
    if not (created := data["tagCreate"]):
        raise RuntimeError(f"Failed to create tag: {name}")
    return created


def update_gallery(
    client: StashClient, gallery_id: str, payload: dict[str, object]
) -> UpdatedGallery:
    data = client.execute(GALLERY_UPDATE, {"input": {"id": gallery_id, **payload}})
    if not (updated := data["galleryUpdate"]):
        raise RuntimeError(f"Failed to update gallery: {gallery_id}")
    return updated


def find_galleries(
    client: StashClient, path_contains: str | None, skip_organized: bool
) -> list[Gallery]:
    gallery_filter: dict[str, object] = {}
    if path_contains:
        gallery_filter["path"] = {"value": path_contains, "modifier": "INCLUDES"}
    if skip_organized:
        gallery_filter["organized"] = False

    data = client.execute(
        FIND_GALLERIES, {"galleryFilter": gallery_filter or None, "filter": {"per_page": -1}}
    )
    return data["findGalleries"]["galleries"]


def find_gallery(client: StashClient, gallery_id: str) -> Gallery | None:
    return client.execute(FIND_GALLERY, {"id": gallery_id})["findGallery"]
