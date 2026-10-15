import gzip
import io
import json
import time
from pathlib import Path
from typing import NamedTuple

from stash_plugin import as_list, as_object, open_url, parse_json

# IMDb's official non-commercial dataset: each episode's tt ID, parent series, season and
# episode number.
DATASET_URL = "https://datasets.imdbws.com/title.episode.tsv.gz"
CACHE_TTL_SECONDS = 24 * 60 * 60


class Episode(NamedTuple):
    season: int
    episode: int
    imdb_id: str


class CacheEntry(NamedTuple):
    fetched: float
    episodes: list[Episode]


def parse_episode(value: object) -> Episode | None:
    match as_list(value):
        case [int(season), int(episode), str(imdb_id)]:
            return Episode(season, episode, imdb_id)
        case _:
            return None


def parse_cache_entry(value: object) -> CacheEntry | None:
    entry = as_object(value) or {}
    fetched, episodes = entry.get("fetched"), as_list(entry.get("episodes"))
    if not isinstance(fetched, int | float):
        return None
    parsed = [episode for item in episodes if (episode := parse_episode(item))]
    # An entry with an unreadable episode is downloaded again rather than trusted.
    return CacheEntry(fetched, parsed) if len(parsed) == len(episodes) else None


def read_cache(cache_path: Path) -> dict[str, CacheEntry]:
    try:
        cache = as_object(parse_json(cache_path.read_text(encoding="utf-8"))) or {}
    except (OSError, ValueError):
        return {}
    return {
        series_id: entry
        for series_id, value in cache.items()
        if (entry := parse_cache_entry(value))
    }


def download_episodes(series_ids: set[str]) -> dict[str, list[Episode]]:
    from urllib.request import Request

    episodes: dict[str, list[Episode]] = {series_id: [] for series_id in series_ids}
    request = Request(DATASET_URL, headers={"User-Agent": "Mozilla/5.0"})
    with open_url(request, 120) as response, gzip.GzipFile(fileobj=response) as raw:
        lines = io.TextIOWrapper(raw, encoding="utf-8")
        _ = next(lines)  # header: tconst parentTconst seasonNumber episodeNumber
        for line in lines:
            episode_id, series_id, season, episode = line.rstrip("\n").split("\t")
            if series_id in series_ids and season.isdigit() and episode.isdigit():
                episodes[series_id].append(Episode(int(season), int(episode), episode_id))

    for series_episodes in episodes.values():
        series_episodes.sort()
    return episodes


def load_episodes(cache_path: Path, series_ids: set[str]) -> dict[str, list[Episode]]:
    cache = read_cache(cache_path)
    now = time.time()
    expired_ids = {
        series_id
        for series_id in series_ids
        if series_id not in cache or now - cache[series_id].fetched > CACHE_TTL_SECONDS
    }
    if expired_ids:
        for series_id, episodes in download_episodes(expired_ids).items():
            cache[series_id] = CacheEntry(now, episodes)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        serialized = {key: entry._asdict() for key, entry in cache.items()}
        _ = cache_path.write_text(json.dumps(serialized), encoding="utf-8")

    return {series_id: cache[series_id].episodes for series_id in series_ids}
