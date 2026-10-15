from typing import TypedDict
from urllib.request import Request

from stash_plugin import Operation, StashClient, as_object, open_url

PLUGIN_ID = "compress-performer-covers"
IMAGE_TIMEOUT = 30


class Performer(TypedDict):
    id: str
    name: str
    image_path: str | None


class _Performers(TypedDict):
    performers: list[Performer]


class _FindPerformers(TypedDict):
    findPerformers: _Performers


class _FindPerformer(TypedDict):
    findPerformer: Performer | None


class SystemStatus(TypedDict):
    ffmpegPath: str | None
    ffprobePath: str | None


class _Configuration(TypedDict):
    plugins: object


class _RuntimeConfig(TypedDict):
    systemStatus: SystemStatus
    configuration: _Configuration


class _Updated(TypedDict):
    id: str


class _PerformerUpdate(TypedDict):
    performerUpdate: _Updated | None


FIND_PERFORMERS = Operation[_FindPerformers]("""
    query FindPerformers($filter: FindFilterType) {
      findPerformers(filter: $filter) {
        performers { id name image_path }
      }
    }
""")

FIND_PERFORMER = Operation[_FindPerformer]("""
    query FindPerformer($id: ID!) {
      findPerformer(id: $id) { id name image_path }
    }
""")

RUNTIME_CONFIG = Operation[_RuntimeConfig]("""
    query RuntimeConfig($pluginIds: [ID!]) {
      systemStatus { ffmpegPath ffprobePath }
      configuration { plugins(include: $pluginIds) }
    }
""")

PERFORMER_UPDATE = Operation[_PerformerUpdate]("""
    mutation PerformerUpdate($input: PerformerUpdateInput!) {
      performerUpdate(input: $input) { id }
    }
""")


def find_performers(client: StashClient) -> list[Performer]:
    data = client.execute(
        FIND_PERFORMERS, {"filter": {"per_page": -1, "sort": "name", "direction": "ASC"}}
    )
    return data["findPerformers"]["performers"]


def find_performer(client: StashClient, performer_id: str) -> Performer | None:
    return client.execute(FIND_PERFORMER, {"id": performer_id})["findPerformer"]


def get_runtime_config(client: StashClient) -> tuple[SystemStatus, dict[str, object]]:
    """Stash's FFmpeg paths and this plugin's settings."""
    data = client.execute(RUNTIME_CONFIG, {"pluginIds": [PLUGIN_ID]})
    plugins = as_object(data["configuration"]["plugins"]) or {}
    return data["systemStatus"], as_object(plugins.get(PLUGIN_ID)) or {}


def update_performer_image(client: StashClient, performer_id: str, data_url: str) -> None:
    data = client.execute(PERFORMER_UPDATE, {"input": {"id": performer_id, "image": data_url}})
    if not data["performerUpdate"]:
        raise RuntimeError(f"Failed to update the cover for performer {performer_id}")


def image_size(client: StashClient, image_path: str) -> int | None:
    """The size the server reports for an image, or None when it does not say."""
    request = Request(client.url(image_path), headers=client.headers(), method="HEAD")
    try:
        with open_url(request, IMAGE_TIMEOUT) as response:
            value = response.headers.get("Content-Length")
    except OSError:
        return None
    return int(value) if value and value.isdigit() else None


def download_image(client: StashClient, image_path: str) -> bytes:
    request = Request(client.url(image_path), headers=client.headers())
    with open_url(request, IMAGE_TIMEOUT) as response:
        return response.read()
