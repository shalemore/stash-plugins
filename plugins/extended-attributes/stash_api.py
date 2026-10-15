import re
from dataclasses import dataclass
from typing import Literal, NotRequired, TypedDict

from stash_plugin import Operation, StashClient

EntityType = Literal["gallery", "group", "image", "scene", "performer"]

TAG_TREE_DEPTH = 5


class TagTree(TypedDict):
    id: str
    name: str
    aliases: list[str]
    custom_fields: object
    # Absent on the deepest level of the queried tree.
    children: NotRequired[list["TagTree"]]


class _Tags(TypedDict):
    tags: list[TagTree]


class _FindTags(TypedDict):
    findTags: _Tags


class _TagRef(TypedDict):
    id: str


class _File(TypedDict):
    # Absent on visual files that are not images.
    path: NotRequired[str]


class _RawItem(TypedDict):
    id: str
    tags: list[_TagRef]
    folder: NotRequired[_File | None]
    files: NotRequired[list[_File]]
    visual_files: NotRequired[list[_File]]


@dataclass(frozen=True)
class Item:
    id: str
    tag_ids: list[str]
    paths: list[str]


_FindItems = dict[str, dict[str, list[_RawItem]]]


@dataclass(frozen=True)
class Entity:
    find_field: str
    results_field: str
    find: Operation[_FindItems]
    bulk_update: Operation[object]


def _entity(name: str, plural: str, path_fields: str = "") -> Entity:
    find_field = f"find{plural.title()}"
    return Entity(
        find_field=find_field,
        results_field=plural,
        find=Operation[_FindItems](f"""
            query Find{plural.title()}($filter: FindFilterType, $ids: [ID!]) {{
              {find_field}(filter: $filter, ids: $ids) {{
                {plural} {{ id {path_fields} tags {{ id }} }}
              }}
            }}
        """),
        bulk_update=Operation[object](f"""
            mutation Bulk{name}Update($input: Bulk{name}UpdateInput!) {{
              bulk{name}Update(input: $input) {{ id }}
            }}
        """),
    )


ENTITIES: dict[EntityType, Entity] = {
    "gallery": _entity("Gallery", "galleries", "folder { path } files { path }"),
    "group": _entity("Group", "groups"),
    "image": _entity("Image", "images", "visual_files { ... on ImageFile { path } }"),
    "scene": _entity("Scene", "scenes", "files { path }"),
    "performer": _entity("Performer", "performers"),
}


def _tag_tree_fields(depth: int) -> str:
    fields = "id name aliases custom_fields"
    if depth == 0:
        return fields
    return f"{fields} children {{ {_tag_tree_fields(depth - 1)} }}"


FIND_ROOT_TAGS = Operation[_FindTags](f"""
    query FindRootTags($tagFilter: TagFilterType, $filter: FindFilterType) {{
      findTags(tag_filter: $tagFilter, filter: $filter) {{
        tags {{ {_tag_tree_fields(TAG_TREE_DEPTH)} }}
      }}
    }}
""")


def find_root_tags(client: StashClient, names: list[str]) -> list[TagTree]:
    data = client.execute(
        FIND_ROOT_TAGS,
        {
            "tagFilter": {
                "name": {
                    "value": f"^({'|'.join(map(re.escape, names))})$",
                    "modifier": "MATCHES_REGEX",
                },
                "parent_count": {"value": 0, "modifier": "EQUALS"},
            },
            "filter": {"per_page": -1},
        },
    )
    return data["findTags"]["tags"]


def _item_paths(item: _RawItem) -> list[str]:
    files = [item.get("folder"), *item.get("files", []), *item.get("visual_files", [])]
    return [path for file in files if file and (path := file.get("path"))]


def find_items(client: StashClient, entity_type: EntityType, ids: list[str] | None) -> list[Item]:
    entity = ENTITIES[entity_type]
    data = client.execute(entity.find, {"filter": {"per_page": -1}, "ids": ids})
    return [
        Item(id=item["id"], tag_ids=[tag["id"] for tag in item["tags"]], paths=_item_paths(item))
        for item in data[entity.find_field][entity.results_field]
    ]


def bulk_update_tags(
    client: StashClient, entity_type: EntityType, ids: list[str], tag_ids: list[str]
) -> None:
    variables = {"input": {"ids": ids, "tag_ids": {"ids": tag_ids, "mode": "SET"}}}
    _ = client.execute(ENTITIES[entity_type].bulk_update, variables)
