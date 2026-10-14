"""Downloads the GraphQL schema of a Stash release from the official repository.

Usage: `python scripts/download_schema.py [tag]` writes schema/stash.graphql, for the latest
release by default. The first line records the release and the fingerprint of the schema
directory, which changes exactly when a schema file changes. Set GITHUB_TOKEN to avoid API rate
limits.
"""

import json
import os
import sys
from urllib.request import Request, urlopen

from graphql import build_schema, print_schema

from catalog import ROOT

API = "https://api.github.com/repos/stashapp/stash"
OUTPUT = ROOT / "schema" / "stash.graphql"


def fetch(path: str, *, raw: bool = False) -> str:
    headers = {
        "Accept": "application/vnd.github.raw+json" if raw else "application/vnd.github+json"
    }
    if token := os.getenv("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(f"{API}/{path}", headers=headers), timeout=60) as response:
        return response.read().decode("utf-8")


def list_directory(path: str, tag: str) -> list[dict[str, str]]:
    entries = json.loads(fetch(f"contents/{path}?ref={tag}"))
    if not isinstance(entries, list):
        raise RuntimeError(f"{path} is not a directory in Stash {tag}")
    return entries


def latest_release() -> str:
    return json.loads(fetch("releases/latest"))["tag_name"]


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
    OUTPUT.write_text(f"# Stash {tag} {fingerprint}\n\n{schema}\n", encoding="utf-8")
    print(f"Downloaded the schema of Stash {tag} ({fingerprint[:10]})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
