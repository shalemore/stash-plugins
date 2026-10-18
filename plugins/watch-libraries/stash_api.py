from typing import TypedDict

from stash_plugin import JOB_QUEUE, Operation, StashClient, as_object, as_str

PLUGIN_ID = "watch-libraries"


class StashConfig(TypedDict):
    path: str
    excludeVideo: bool
    excludeImage: bool


class _General(TypedDict):
    stashes: list[StashConfig]
    videoExtensions: list[str]
    imageExtensions: list[str]
    excludes: list[str]
    imageExcludes: list[str]


class _Defaults(TypedDict):
    scan: dict[str, bool | None] | None


class _Configuration(TypedDict):
    plugins: object
    general: _General
    defaults: _Defaults


class _WatchConfig(TypedDict):
    configuration: _Configuration


class _MetadataScan(TypedDict):
    metadataScan: str


WATCH_CONFIG = Operation[_WatchConfig]("""
    query WatchConfig($pluginIds: [ID!]) {
      configuration {
        plugins(include: $pluginIds)
        general {
          stashes { path excludeVideo excludeImage }
          videoExtensions
          imageExtensions
          excludes
          imageExcludes
        }
        defaults {
          scan {
            scanGenerateCovers
            scanGeneratePreviews
            scanGenerateImagePreviews
            scanGenerateSprites
            scanGeneratePhashes
            scanGenerateImagePhashes
            scanGenerateThumbnails
            scanGenerateClipPreviews
          }
        }
      }
    }
""")

METADATA_SCAN = Operation[_MetadataScan]("""
    mutation MetadataScan($input: ScanMetadataInput!) {
      metadataScan(input: $input)
    }
""")


class WatchConfig(TypedDict):
    settings: dict[str, object]
    libraries: list[StashConfig]
    video_extensions: list[str]
    image_extensions: list[str]
    video_excludes: list[str]
    image_excludes: list[str]
    scan_options: dict[str, bool | None]


def get_watch_config(client: StashClient) -> WatchConfig:
    configuration = client.execute(WATCH_CONFIG, {"pluginIds": [PLUGIN_ID]})["configuration"]
    plugins = as_object(configuration["plugins"]) or {}
    general = configuration["general"]
    return {
        "settings": as_object(plugins.get(PLUGIN_ID)) or {},
        "libraries": general["stashes"],
        "video_extensions": general["videoExtensions"],
        "image_extensions": general["imageExtensions"],
        "video_excludes": general["excludes"],
        "image_excludes": general["imageExcludes"],
        "scan_options": configuration["defaults"]["scan"] or {},
    }


def scan_is_busy(client: StashClient) -> bool:
    """Whether a scan is queued or running, so that new files can wait and go in one batch."""
    jobs = client.execute(JOB_QUEUE, {})["jobQueue"] or []
    return any(
        job["status"] in {"READY", "RUNNING", "STOPPING"} and "scan" in job["description"].lower()
        for job in jobs
    )


def scan_paths(client: StashClient, paths: list[str], options: dict[str, bool | None]) -> str:
    """Starts a scan of the files and directories with the scan options that are the defaults in
    Stash. Returns the job's ID."""
    data = client.execute(METADATA_SCAN, {"input": {**options, "paths": paths}})
    return as_str(data["metadataScan"]) or ""
