"""Downloads the GraphQL schema of a Stash release from the official repository.

Usage: `python scripts/download_schema.py [tag]` writes schema/stash.graphql, for the latest
release by default. The first line records the release and the fingerprint of the schema
directory, which changes exactly when a schema file changes. Set GITHUB_TOKEN to avoid API rate
limits.
"""

import json
import os
import sys
from http.client import HTTPResponse
from typing import TypedDict, cast
from urllib.request import Request, urlopen

from graphql import build_schema, print_schema

from catalog import ROOT

API = "https://api.github.com/repos/stashapp/stash"
OUTPUT = ROOT / "schema" / "stash.graphql"


class Entry(TypedDict):
    name: str
    sha: str


class Release(TypedDict):
    tag_name: str


def fetch(path: str, *, raw: bool = False) -> str:
    headers = {
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json"
    }
    if token := os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    # typeshed types urlopen() as Any; for https URLs it returns an HTTPResponse.
    request = Request(f"{API}/{path}", headers=headers)
    with cast(HTTPResponse, urlopen(request, timeout=60)) as response:
        return response.read().decode("utf-8")


def fetch_json(path: str) -> object:
    return cast(object, json.loads(fetch(path)))


# The casts below rely on the documented shapes of GitHub's REST API.
def list_directory(path: str, tag: str) -> list[Entry]:
    entries = fetch_json(f"contents/{path}?ref={tag}")
    if not isinstance(entries, list):
        raise RuntimeError(f"{path} is not a directory in Stash {tag}")
    return cast("list[Entry]", entries)


def latest_release() -> str:
    return cast(Release, fetch_json("releases/latest"))["tag_name"]


def schema_fingerprint(tag: str) -> str:
    for entry in list_directory("graphql", tag):
        if entry["name"] == "schema":
            return entry["sha"]
    raise RuntimeError(f"Stash {tag} has no graphql/schema directory")


def schema_sources(tag: str) -> list[str]:
    files = ["schema.graphql"] + [
        f"types/{entry['name']}" for entry in list_directory("graphql/schema/types", tag)
    ]
    return [fetch(f"contents/graphql/schema/{file}?ref={tag}", raw=True) for file in files]


def main(tag: str | None) -> None:
    tag = tag or latest_release()
    fingerprint = schema_fingerprint(tag)
    # Building the schema validates it; printing normalizes the order and formatting.
    schema = print_schema(build_schema("\n".join(schema_sources(tag))))
    OUTPUT.parent.mkdir(exist_ok=True)
    content = f"# Stash {tag} {fingerprint}\n\n{schema}\n"
    _ = OUTPUT.write_text(content, encoding="utf-8", newline="\n")
    print(f"Downloaded the schema of Stash {tag} ({fingerprint[:10]})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
