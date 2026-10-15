import re
import sys
import tempfile
from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import NamedTuple

from imdb_episodes import Episode, load_episodes
from stash_api import (
    TIMEOUT,
    Scene,
    Subtype,
    create_group,
    find_group_subtypes,
    find_groups,
    find_scenes,
    scrape_group_url,
    scrape_scene_url,
    update_scene,
)
from stash_plugin import (
    FAILURES,
    PluginInput,
    Progress,
    StashClient,
    as_bool,
    as_list,
    emit_info,
    emit_warn,
    run_plugin,
)

IMDB_ID_RE = re.compile(r"\[imdbid=(tt\d+)\]", re.IGNORECASE)
SEASON_EPISODES_RE = re.compile(r"[Ss](\d{1,2})((?:[ ._-]?[Ee]\d{1,3})+)")
SEASON_X_EPISODE_RE = re.compile(r"\b(\d{1,2})x(\d{1,3})(?:-(\d{1,3}))?\b", re.IGNORECASE)
EPISODE_NUMBER_RE = re.compile(r"[Ee](\d{1,3})")

# Name or alias of the subtype tag under __GROUP__ that every IMDb group gets. Put this alias
# on the subtype. extended-attributes adds its attribute defaults when the group is created.
GROUP_TYPE_TAG = "IMDb"

GROUP_SCRAPED_FIELDS = ("name", "date", "director", "synopsis", "front_image", "back_image")
SCENE_SCRAPED_FIELDS = {
    "title": "title",
    "code": "code",
    "details": "details",
    "director": "director",
    "date": "date",
    "image": "cover_image",
}


def imdb_url(imdb_id: str) -> str:
    return f"https://www.imdb.com/title/{imdb_id}/"


@contextmanager
def exclusive_lock(lock_path: Path) -> Generator[None, None, None]:
    """Let only one instance run at a time.

    A scan is multi-threaded, so several files of one title fire the hook concurrently. Without this
    lock they would all find that the group does not exist yet and each create one. The lock is
    released when the process exits, so it never goes stale.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock_file:
        if sys.platform == "win32":
            import msvcrt

            # LK_LOCK gives up after about 10 seconds, so keep retrying until it succeeds.
            while True:
                try:
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    continue
        else:
            import fcntl

            fcntl.flock(lock_file, fcntl.LOCK_EX)
        yield


class SceneEntry(NamedTuple):
    scene: Scene
    folder: str
    file_name: str
    episode_key: tuple[int, int] | None


def parse_episode(file_name: str) -> tuple[int, int] | None:
    if match := SEASON_EPISODES_RE.search(file_name):
        # Multi-episode files (e.g. S01E23-E24) are treated as the last episode.
        *_, last = EPISODE_NUMBER_RE.finditer(match[2])
        return int(match[1]), int(last[1])
    if match := SEASON_X_EPISODE_RE.search(file_name):
        return int(match[1]), int(match[3] or match[2])
    return None


def find_imdb_entry(scene: Scene) -> tuple[str, SceneEntry] | None:
    # The folder of a title carries the IMDb ID: <anything>/<title> [imdbid=ttXXXX]/...
    for file in scene["files"]:
        *parts, file_name = re.split(r"[\\/]", file["path"])
        for folder in filter(None, parts):
            if match := IMDB_ID_RE.search(folder):
                return match[1], SceneEntry(scene, folder, file_name, parse_episode(file_name))
    return None


def group_entries_by_imdb_id(scenes: list[Scene]) -> dict[str, list[SceneEntry]]:
    entries_by_imdb_id: defaultdict[str, list[SceneEntry]] = defaultdict(list)
    for scene in scenes:
        if found := find_imdb_entry(scene):
            entries_by_imdb_id[found[0]].append(found[1])
    return entries_by_imdb_id


def fallback_group_name(folder: str) -> str:
    name = re.sub(r"\s*\[[^\]]*\]", "", folder)
    return re.sub(r"\s*\(\d{4}\)$", "", name).strip()


def find_type_tag(client: StashClient) -> Subtype | None:
    """The subtype tag under `__GROUP__` that is named or aliased `GROUP_TYPE_TAG`, if any."""
    wanted = GROUP_TYPE_TAG.casefold()
    return next(
        (
            tag
            for tag in find_group_subtypes(client)
            if wanted in (name.casefold() for name in [tag["name"], *tag["aliases"]])
        ),
        None,
    )


def ensure_entry_group(
    client: StashClient, imdb_id: str, entry: SceneEntry, dry_run: bool
) -> str | None:
    url = imdb_url(imdb_id)
    # An existing Group's link may lack the trailing slash, so match by IMDb ID instead of
    # comparing exactly.
    url_regex = rf"imdb\.com/title/{imdb_id}(?:[/?#]|$)"
    existing = find_groups(client, {"url": {"value": url_regex, "modifier": "MATCHES_REGEX"}})
    if existing:
        return existing[0]["id"]

    type_tag = find_type_tag(client)
    if type_tag is None:
        emit_warn(
            f"No subtype tag named or aliased '{GROUP_TYPE_TAG}' under __GROUP__, "
            "so the group gets no tag"
        )
    if dry_run:
        emit_info(
            f"Would create entry group from {url} (tag: {type_tag['name'] if type_tag else '-'})"
        )
        return None

    scraped: dict[str, object] = {}
    try:
        scraped = scrape_group_url(client, url) or {}
    except FAILURES as exc:
        emit_warn(f"Group scrape failed, creating it from the folder name: {url} ({exc})")

    group_input: dict[str, object] = {
        "name": fallback_group_name(entry.folder),
        **{key: scraped[key] for key in GROUP_SCRAPED_FIELDS if scraped.get(key)},
    }
    group_input["urls"] = scraped.get("urls") or [url]
    if type_tag:
        group_input["tag_ids"] = [type_tag["id"]]
    created = create_group(client, group_input)
    emit_info(f"Created group: {created['name']} ({url})")
    return created["id"]


def build_scene_update(
    client: StashClient,
    scene: Scene,
    group_id: str,
    scene_index: int | None,
    episode: Episode | None,
    dry_run: bool,
) -> dict[str, object]:
    update: dict[str, object] = {"id": scene["id"]}

    current_indexes = {item["group"]["id"]: item["scene_index"] for item in scene["groups"]}
    if group_id not in current_indexes or current_indexes[group_id] != scene_index:
        other_groups = [
            {"group_id": group_id_, "scene_index": index}
            for group_id_, index in current_indexes.items()
            if group_id_ != group_id
        ]
        update["groups"] = [*other_groups, {"group_id": group_id, "scene_index": scene_index}]

    if episode:
        url = imdb_url(episode.imdb_id)
        urls = [url, *scene["urls"]]
        if not scene["title"] and not dry_run:
            scraped = scrape_scene_url(client, url) or {}
            update.update(
                {
                    target: scraped[source]
                    for source, target in SCENE_SCRAPED_FIELDS.items()
                    if scraped.get(source)
                }
            )
            urls = [url, *map(str, as_list(scraped.get("urls"))), *scene["urls"]]
        unique_urls = list(dict.fromkeys(urls))
        if unique_urls != scene["urls"]:
            update["urls"] = unique_urls

    return update


@dataclass
class SeriesResult:
    updated: int = 0
    skipped: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)


def sync_series(
    client: StashClient,
    imdb_id: str,
    entries: list[SceneEntry],
    episodes: list[Episode],
    dry_run: bool,
    progress: Progress,
) -> SeriesResult:
    group_id = ensure_entry_group(client, imdb_id, entries[0], dry_run)

    episode_by_key = {(item.season, item.episode): item for item in episodes}
    # scene_index only accumulates over seasons already in the library; missing seasons take no
    # slot.
    owned_seasons = {entry.episode_key[0] for entry in entries if entry.episode_key}
    index_by_key = {
        (item.season, item.episode): index
        for index, item in enumerate(
            (item for item in episodes if item.season in owned_seasons and item.episode > 0),
            start=1,
        )
    }
    result = SeriesResult()

    for entry in entries:
        scene, file_name, key = entry.scene, entry.file_name, entry.episode_key
        progress.step()
        if group_id is None:
            result.skipped += 1
            continue

        if key and key not in episode_by_key:
            emit_warn(f"Episode {key} of {imdb_id} was not found in the IMDb dataset: {file_name}")
        try:
            update = build_scene_update(
                client,
                scene,
                group_id,
                index_by_key.get(key) if key else None,
                episode_by_key.get(key) if key else None,
                dry_run,
            )
            if len(update) == 1:
                result.skipped += 1
                continue
            if not dry_run:
                update_scene(client, update)
            result.updated += 1
            emit_info(
                f"{'Would update' if dry_run else 'Updated'} scene {scene['id']}: {file_name}"
            )
        except FAILURES as exc:
            result.failed.append({"id": scene["id"], "path": file_name, "reason": str(exc)})
            emit_warn(f"Failed scene {scene['id']}: {file_name} ({exc})")

    return result


def run(plugin_input: PluginInput) -> object:
    hook = plugin_input.hook
    dry_run = as_bool(plugin_input.args.get("dry_run"), False)
    client = StashClient.connect(plugin_input, timeout=TIMEOUT)

    scene_ids = [hook.id] if hook and hook.id is not None else None
    entries_by_imdb_id = group_entries_by_imdb_id(find_scenes(client, scene_ids))
    if not entries_by_imdb_id:
        return {"dry_run": dry_run, "series": {}}

    cache_path = Path(plugin_input.config_dir or tempfile.gettempdir()) / "cache"
    # Scenes outside IMDb folders returned above and never queue. The rest run one at a time so
    # concurrent hooks cannot create duplicate groups.
    with exclusive_lock(cache_path / "imdb-updater.lock"):
        if scene_ids:
            # A new scene can change the scene_index of the other scenes of the same title, so the
            # whole title is processed. Read again inside the lock to see what the previous
            # instance just created.
            entries_by_imdb_id = group_entries_by_imdb_id(
                find_scenes(client, imdb_ids=list(entries_by_imdb_id))
            )

        tv_series_ids = {
            imdb_id
            for imdb_id, entries in entries_by_imdb_id.items()
            if any(entry.episode_key for entry in entries)
        }
        episodes = load_episodes(cache_path / "imdb-updater-episodes.json", tv_series_ids)

        progress = Progress(total=sum(map(len, entries_by_imdb_id.values())))
        emit_info(f"Processing {progress.total} scenes of {len(entries_by_imdb_id)} IMDb entries")
        output = {
            imdb_id: asdict(
                sync_series(client, imdb_id, entries, episodes.get(imdb_id, []), dry_run, progress)
            )
            for imdb_id, entries in entries_by_imdb_id.items()
        }
    return {"dry_run": dry_run, "series": output}


if __name__ == "__main__":
    run_plugin(run)
