from typing import TypedDict

from stash_plugin import Operation, StashClient

IMDB_PATH_REGEX = r"\[imdbid=tt\d+\]"
# Root tag of the Group types, shared with extended-attributes.
GROUP_ROOT_TAG = "__GROUP__"
# Scrapers fetch web pages and covers, which can take over a minute.
TIMEOUT = 180


class _Ref(TypedDict):
    id: str


class _File(TypedDict):
    path: str


class SceneGroup(TypedDict):
    group: _Ref
    scene_index: int | None


class Scene(TypedDict):
    id: str
    title: str | None
    urls: list[str]
    files: list[_File]
    groups: list[SceneGroup]


class _Scenes(TypedDict):
    scenes: list[Scene]


class _FindScenes(TypedDict):
    findScenes: _Scenes


class Group(TypedDict):
    id: str
    name: str


class _Groups(TypedDict):
    groups: list[Group]


class _FindGroups(TypedDict):
    findGroups: _Groups


class Subtype(TypedDict):
    id: str
    name: str
    aliases: list[str]


class _RootTag(TypedDict):
    children: list[Subtype]


class _RootTags(TypedDict):
    tags: list[_RootTag]


class _FindRootTags(TypedDict):
    findTags: _RootTags


class _GroupCreate(TypedDict):
    groupCreate: Group


class _ScrapeGroup(TypedDict):
    scrapeGroupURL: dict[str, object] | None


class _ScrapeScene(TypedDict):
    scrapeSceneURL: dict[str, object] | None


FIND_SCENES = Operation[_FindScenes]("""
    query FindScenes($sceneFilter: SceneFilterType, $filter: FindFilterType, $ids: [ID!]) {
      findScenes(scene_filter: $sceneFilter, filter: $filter, ids: $ids) {
        scenes { id title urls files { path } groups { group { id } scene_index } }
      }
    }
""")

FIND_GROUPS = Operation[_FindGroups]("""
    query FindGroups($groupFilter: GroupFilterType, $filter: FindFilterType) {
      findGroups(group_filter: $groupFilter, filter: $filter) {
        groups { id name }
      }
    }
""")

FIND_GROUP_SUBTYPES = Operation[_FindRootTags]("""
    query FindGroupSubtypes($tagFilter: TagFilterType!) {
      findTags(tag_filter: $tagFilter, filter: { per_page: -1 }) {
        tags { children { id name aliases } }
      }
    }
""")

GROUP_CREATE = Operation[_GroupCreate]("""
    mutation GroupCreate($input: GroupCreateInput!) {
      groupCreate(input: $input) { id name }
    }
""")

SCENE_UPDATE = Operation[object]("""
    mutation SceneUpdate($input: SceneUpdateInput!) {
      sceneUpdate(input: $input) { id }
    }
""")

SCRAPE_GROUP = Operation[_ScrapeGroup]("""
    query ScrapeGroup($url: String!) {
      scrapeGroupURL(url: $url) {
        name aliases duration date director urls synopsis front_image back_image
      }
    }
""")

SCRAPE_SCENE = Operation[_ScrapeScene]("""
    query ScrapeScene($url: String!) {
      scrapeSceneURL(url: $url) { title code details director urls date image }
    }
""")


def find_scenes(client: StashClient, ids: list[str] | None = None) -> list[Scene]:
    """The scenes in IMDb folders, optionally only those with the given IDs."""
    data = client.execute(
        FIND_SCENES,
        {
            "sceneFilter": {"path": {"value": IMDB_PATH_REGEX, "modifier": "MATCHES_REGEX"}},
            "filter": {"per_page": -1},
            "ids": ids,
        },
    )
    return data["findScenes"]["scenes"]


def find_groups(client: StashClient, group_filter: dict[str, object]) -> list[Group]:
    data = client.execute(FIND_GROUPS, {"groupFilter": group_filter, "filter": {"per_page": -1}})
    return data["findGroups"]["groups"]


def find_group_subtypes(client: StashClient) -> list[Subtype]:
    """The subtype tags directly under the `__GROUP__` root tag."""
    data = client.execute(
        FIND_GROUP_SUBTYPES,
        {
            "tagFilter": {
                "name": {"value": GROUP_ROOT_TAG, "modifier": "EQUALS"},
                "parents": {"value": [], "modifier": "IS_NULL"},
            }
        },
    )
    return [child for tag in data["findTags"]["tags"] for child in tag["children"]]


def create_group(client: StashClient, group_input: dict[str, object]) -> Group:
    return client.execute(GROUP_CREATE, {"input": group_input})["groupCreate"]


def update_scene(client: StashClient, scene_input: dict[str, object]) -> None:
    _ = client.execute(SCENE_UPDATE, {"input": scene_input})


def scrape_group_url(client: StashClient, url: str) -> dict[str, object] | None:
    return client.execute(SCRAPE_GROUP, {"url": url})["scrapeGroupURL"]


def scrape_scene_url(client: StashClient, url: str) -> dict[str, object] | None:
    return client.execute(SCRAPE_SCENE, {"url": url})["scrapeSceneURL"]
