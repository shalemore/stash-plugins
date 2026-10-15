"""What every plugin needs: Stash's input, logging to Stash and its GraphQL API.

Plugins import this as a sibling module. It lives here once, and the build copies it into each
plugin's zip.

Stash runs a hook's plugin for every saved object and waits for it, so a hook with nothing to do
must exit fast. Importing a module like urllib.request alone takes ~80ms, so this module imports
only what reading the input needs, and plugins import their own modules after deciding to work.
"""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING, Generic, NamedTuple, TypedDict, TypeVar, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from http.client import HTTPResponse
    from urllib.request import Request

# The errors a plugin reports to Stash as a failed run instead of a traceback. OSError covers
# network errors and timeouts.
FAILURES = (OSError, RuntimeError, ValueError, KeyError)

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)


def _log(level: str, message: str) -> None:
    # Stash reads stderr line by line and takes the level from each line's own prefix, so every
    # line of a message carries it.
    lines = message.splitlines() or [""]
    print("\n".join(f"\x01{level}\x02{line}" for line in lines), file=sys.stderr, flush=True)


def emit_debug(message: str) -> None:
    _log("d", message)


def emit_info(message: str) -> None:
    _log("i", message)


def emit_warn(message: str) -> None:
    _log("w", message)


def emit_progress(progress: float) -> None:
    _log("p", f"{max(0.0, min(1.0, progress)):.4f}")


class Progress:
    """Reports to Stash how many of the total steps are done."""

    def __init__(self, total: int) -> None:
        self.total: int = total
        self.done: int = 0

    def step(self) -> None:
        self.done += 1
        emit_progress(self.done / self.total)


def parse_json(text: str | bytes) -> object:
    return cast(object, json.loads(text))


def as_object(value: object) -> dict[str, object] | None:
    # A decoded JSON object always has string keys.
    return cast("dict[str, object]", value) if isinstance(value, dict) else None


def as_list(value: object) -> list[object]:
    return cast("list[object]", value) if isinstance(value, list) else []


def as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def as_bool(value: object, default: bool) -> bool:
    """Task arguments arrive as booleans or as the strings "true" and "false"."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def open_url(request: Request, timeout: float) -> HTTPResponse:
    from urllib.request import urlopen

    # typeshed types urlopen() as Any; for http(s) URLs it returns an HTTPResponse.
    return cast("HTTPResponse", urlopen(request, timeout=timeout))


class HookContext(NamedTuple):
    id: str | None
    input: dict[str, object] | None
    input_fields: list[str]


class PluginInput(NamedTuple):
    graphql_url: str
    config_dir: str | None
    cookie: str | None
    args: dict[str, object]

    @property
    def hook(self) -> HookContext | None:
        """The hook that triggered this run, or None when it runs as a task."""
        context = as_object(self.args.get("hookContext"))
        if not context:
            return None
        hook_id = context.get("id")
        return HookContext(
            id=None if hook_id is None else str(hook_id),
            input=as_object(context.get("input")),
            input_fields=[str(field) for field in as_list(context.get("inputFields"))],
        )


def graphql_url(connection: Mapping[str, object]) -> str:
    explicit_url = str(connection.get("GraphQLURL") or "").strip()
    if explicit_url:
        return explicit_url
    scheme = connection.get("Scheme") or "http"
    host = connection.get("Host") or "127.0.0.1"
    if host == "0.0.0.0":
        host = "127.0.0.1"
    port = connection.get("Port") or 9999
    return f"{scheme}://{host}:{port}/graphql"


def session_cookie(cookie: object) -> str | None:
    cookie = as_object(cookie) or {}
    name, value = cookie.get("Name"), cookie.get("Value")
    return f"{name}={value}" if name and value else None


def read_plugin_input() -> PluginInput:
    raw = sys.stdin.read().strip()
    data = (as_object(parse_json(raw)) if raw else None) or {}
    connection = as_object(data.get("server_connection")) or {}
    return PluginInput(
        graphql_url=graphql_url(connection),
        config_dir=as_str(connection.get("Dir")) or None,
        cookie=session_cookie(connection.get("SessionCookie")),
        args=as_object(data.get("args")) or {},
    )


def read_api_key(config_dir: str | None) -> str | None:
    import re

    if environment_key := os.getenv("STASH_KEY"):
        return environment_key
    if not config_dir:
        return None
    try:
        with open(os.path.join(config_dir, "config.yml"), encoding="utf-8") as config:
            match = re.search(r"^api_key:\s*(\S+)", config.read(), re.MULTILINE)
    except FileNotFoundError:
        return None
    return match[1] if match else None


Data = TypeVar("Data")


class Operation(Generic[Data]):
    """A GraphQL document together with the shape of the data it returns.

    The data type is written by hand from the document's selection set, so it is checked against
    the schema only by reading it.
    """

    def __init__(self, document: str) -> None:
        self.document: str = document


class StashClient(NamedTuple):
    graphql_url: str
    api_key: str | None
    cookie: str | None
    timeout: float = 60

    @classmethod
    def connect(cls, plugin_input: PluginInput, timeout: float = 60) -> StashClient:
        return cls(
            graphql_url=plugin_input.graphql_url,
            api_key=read_api_key(plugin_input.config_dir),
            cookie=plugin_input.cookie,
            timeout=timeout,
        )

    def headers(self) -> dict[str, str]:
        # Some reverse proxies in front of Stash only let browsers through.
        origin = self.url("/").rstrip("/")
        headers = {"User-Agent": BROWSER_USER_AGENT, "Origin": origin, "Referer": f"{origin}/"}
        if self.api_key:
            headers["ApiKey"] = self.api_key
        if self.cookie:
            headers["Cookie"] = self.cookie
        return headers

    def url(self, path: str) -> str:
        """Resolves a path that Stash returned, such as an image path, against its address."""
        from urllib.parse import urljoin, urlsplit

        if path.lower().startswith(("http://", "https://")):
            return path
        parsed = urlsplit(self.graphql_url)
        return urljoin(f"{parsed.scheme}://{parsed.netloc}/", path)

    def execute(self, operation: Operation[Data], variables: Mapping[str, object]) -> Data:
        from urllib.request import Request

        payload = json.dumps({"query": operation.document, "variables": variables})
        headers = {
            **self.headers(),
            "Content-Type": "application/json",
            "Accept": "application/graphql-response+json, application/json",
        }
        request = Request(self.graphql_url, payload.encode(), headers, method="POST")
        with open_url(request, self.timeout) as response:
            body = as_object(parse_json(response.read())) or {}

        if errors := body.get("errors"):
            raise RuntimeError(json.dumps(errors, ensure_ascii=False))
        if "data" not in body:
            raise RuntimeError("Stash returned no data")
        # The schema guarantees that the data has the shape of the document's selection set.
        return cast(Data, body["data"])


class _Job(TypedDict):
    status: str
    description: str


class _JobQueue(TypedDict):
    jobQueue: list[_Job] | None


class _RunPluginTask(TypedDict):
    runPluginTask: str


JOB_QUEUE = Operation[_JobQueue]("query JobQueue { jobQueue { status description } }")

RUN_PLUGIN_TASK = Operation[_RunPluginTask]("""
    mutation RunPluginTask($pluginId: ID!, $taskName: String!, $description: String, $args: Map) {
      runPluginTask(
        plugin_id: $pluginId, task_name: $taskName, description: $description, args_map: $args
      )
    }
""")


def queue_task(
    client: StashClient,
    plugin_id: str,
    task_name: str,
    description: str,
    args: Mapping[str, object] | None = None,
) -> bool:
    """Queues a task of the plugin as a background job in Stash, unless the same job is already
    waiting to start. Returns whether it queued one.

    Hooks use this to hand slow work to Stash's job queue, so that saving does not wait for it.
    """
    jobs = client.execute(JOB_QUEUE, {})["jobQueue"] or []
    # Stash prefixes the description, so the job's description ends with ours.
    if any(job["status"] == "READY" and job["description"].endswith(description) for job in jobs):
        return False
    _ = client.execute(
        RUN_PLUGIN_TASK,
        {"pluginId": plugin_id, "taskName": task_name, "description": description, "args": args},
    )
    return True


def run_plugin(run: Callable[[PluginInput], object]) -> None:
    """Runs a plugin and reports its output, or the error that stopped it, to Stash."""
    try:
        output = run(read_plugin_input())
    except FAILURES as exc:
        print(json.dumps({"error": str(exc), "output": None}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps({"error": None, "output": output}, ensure_ascii=False))
