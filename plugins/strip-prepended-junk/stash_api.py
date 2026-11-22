"""GraphQL operations and helpers for the Strip Prepended Junk plugin."""

from stash_plugin import Operation, StashClient, as_list, as_object, as_str

SCENE_FILES = Operation[dict[str, object]]("""
    query StripPrependedJunkSceneFiles($filter: FindFilterType) {
      findScenes(filter: $filter) {
        scenes {
          id
          files {
            path
          }
        }
      }
    }
""")

# Regenerating sprites and phashes is the whole point: the broken header left them failing, and
# the scan drops the stale phash by itself when the checksum changes.
SCAN = Operation[object]("""
    mutation StripPrependedJunkScan($input: ScanMetadataInput!) {
      metadataScan(input: $input)
    }
""")


def find_scene_files(client: StashClient) -> list[tuple[str, str]]:
    """Every scene with each of its file paths."""
    data = client.execute(SCENE_FILES, {"filter": {"per_page": -1}})
    scenes = as_list((as_object(data.get("findScenes")) or {}).get("scenes"))
    files: list[tuple[str, str]] = []
    for scene in scenes:
        scene_object = as_object(scene) or {}
        scene_id = as_str(scene_object.get("id")) or ""
        for file in as_list(scene_object.get("files")):
            path = as_str((as_object(file) or {}).get("path"))
            if path:
                files.append((scene_id, path))
    return files


def scan_paths(client: StashClient, paths: list[str]) -> None:
    """Rescan the given files so Stash picks up the new checksum and regenerates the artifacts."""
    if not paths:
        return
    _ = client.execute(
        SCAN,
        {
            "input": {
                "paths": paths,
                "rescan": True,
                "scanGenerateCovers": True,
                "scanGenerateSprites": True,
                "scanGeneratePhashes": True,
            }
        },
    )
