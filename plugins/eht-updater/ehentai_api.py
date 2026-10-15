import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html import unescape
from urllib.error import URLError
from urllib.request import Request

from stash_plugin import as_list, as_object, as_str, open_url, parse_json

TIMEOUT = 30


@dataclass(frozen=True)
class GalleryMeta:
    title: str
    posted: str
    rating: str
    tags: list[str]

    @property
    def date(self) -> str:
        return datetime.fromtimestamp(int(self.posted), tz=UTC).strftime("%Y-%m-%d")

    @property
    def rating100(self) -> int:
        return round(float(self.rating)) * 20


def http_json(url: str, payload: dict[str, object]) -> object:
    headers = {"Content-Type": "application/json"}
    request = Request(url, json.dumps(payload).encode(), headers, method="POST")
    with open_url(request, TIMEOUT) as response:
        return parse_json(response.read())


def http_text(url: str) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with open_url(request, TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


def extract_gallery_language(gid: int, token: str) -> str | None:
    html = http_text(f"https://e-hentai.org/g/{gid}/{token}/")
    match = re.search(
        r'<td class="gdt1">Language:</td><td class="gdt2">([\s\S]*?)</td>', html, re.IGNORECASE
    )
    if not match:
        return None

    language = unescape(re.sub(r"<[^>]+>", "", match[1])).strip().lower()
    return f"language:{language}" if language else None


def parse_meta(gid: int, token: str, data: object) -> GalleryMeta:
    items = as_list((as_object(data) or {}).get("gmetadata"))
    if len(items) != 1:
        raise RuntimeError(f"E-Hentai metadata not found: {gid}_{token}")

    meta = as_object(items[0]) or {}
    title, posted, rating = (as_str(meta.get(key)) for key in ("title", "posted", "rating"))
    if title is None or posted is None or rating is None:
        detail = meta.get("error") or "the response lacks the title, date or rating"
        raise RuntimeError(f"E-Hentai metadata not found: {gid}_{token} ({detail})")
    tags = [str(tag) for tag in as_list(meta.get("tags"))]
    return GalleryMeta(title=title, posted=posted, rating=rating, tags=tags)


def fetch_ehentai(gid: int, token: str) -> tuple[GalleryMeta, list[str]]:
    logs: list[str] = []
    payload: dict[str, object] = {"method": "gdata", "gidlist": [[gid, token]], "namespace": 1}
    meta = parse_meta(gid, token, http_json("https://api.e-hentai.org/api.php", payload))
    logs.append("Fetched metadata from the E-Hentai API")
    if any(tag.startswith("language:") for tag in meta.tags):
        logs.append("Language tag was present in the E-Hentai API response")
        return meta, logs

    logs.append("Language tag was missing in the API response; trying gallery page fallback")
    try:
        fallback = extract_gallery_language(gid, token)
    except (URLError, TimeoutError):
        fallback = None
        logs.append("Gallery page fallback request failed while resolving the language tag")

    if fallback:
        meta = replace(meta, tags=[fallback, *meta.tags])
        logs.append(f"Gallery page fallback resolved the language tag successfully: {fallback}")
    else:
        logs.append("No language tag was found; the gallery will be updated without a language tag")
    return meta, logs
