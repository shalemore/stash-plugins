import pytest
from stash_plugin import (
    HookContext,
    PluginInput,
    Progress,
    StashClient,
    as_bool,
    emit_warn,
    graphql_url,
)


def plugin_input(args: dict[str, object]) -> PluginInput:
    return PluginInput(
        graphql_url="http://stash:9999/graphql", config_dir=None, cookie=None, args=args
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, True),
        (True, True),
        (False, False),
        ("true", True),
        (" TRUE ", True),
        ("false", False),
    ],
)
def test_task_arguments_are_booleans_or_strings(value: object, expected: bool):
    assert as_bool(value, True) is expected


def test_graphql_url_replaces_the_wildcard_host():
    assert graphql_url({"Scheme": "https", "Host": "0.0.0.0", "Port": 443}) == (
        "https://127.0.0.1:443/graphql"
    )
    assert graphql_url({}) == "http://127.0.0.1:9999/graphql"
    assert graphql_url({"GraphQLURL": " http://x/graphql ", "Port": 1}) == "http://x/graphql"


def test_a_task_has_no_hook():
    assert plugin_input({"dry_run": "true"}).hook is None
    assert plugin_input({"hookContext": {}}).hook is None


def test_hook_context_is_parsed():
    hook = plugin_input(
        {"hookContext": {"id": 7, "input": {"ids": ["7"]}, "inputFields": ["ids", "tag_ids"]}}
    ).hook
    assert hook == HookContext(id="7", input={"ids": ["7"]}, input_fields=["ids", "tag_ids"])


def test_client_resolves_paths_against_the_stash_address():
    client = StashClient("http://stash:9999/graphql", api_key="key", cookie="session=1")
    assert client.url("/performer/1/image") == "http://stash:9999/performer/1/image"
    assert client.url("https://cdn/x.jpg") == "https://cdn/x.jpg"
    assert client.headers()["Origin"] == "http://stash:9999"
    assert client.headers()["ApiKey"] == "key"
    assert client.headers()["Cookie"] == "session=1"


def test_every_line_of_a_log_message_carries_its_level(capsys: pytest.CaptureFixture[str]):
    emit_warn("first\nsecond")
    assert capsys.readouterr().err == "\x01w\x02first\n\x01w\x02second\n"


def test_progress_logs_the_step_at_every_five_percent(capsys: pytest.CaptureFixture[str]):
    progress = Progress(20, label="Gallery Cluster: clustering images")
    for _ in range(20):
        progress.step()

    logged = [
        line.split("\x02", 1)[1]
        for line in capsys.readouterr().err.splitlines()
        if line.startswith("\x01i\x02")
    ]
    assert logged == [
        "Gallery Cluster: clustering images 1/20 (5%)",
        "Gallery Cluster: clustering images 2/20 (10%)",
        "Gallery Cluster: clustering images 3/20 (15%)",
        "Gallery Cluster: clustering images 4/20 (20%)",
        "Gallery Cluster: clustering images 5/20 (25%)",
        "Gallery Cluster: clustering images 6/20 (30%)",
        "Gallery Cluster: clustering images 7/20 (35%)",
        "Gallery Cluster: clustering images 8/20 (40%)",
        "Gallery Cluster: clustering images 9/20 (45%)",
        "Gallery Cluster: clustering images 10/20 (50%)",
        "Gallery Cluster: clustering images 11/20 (55%)",
        "Gallery Cluster: clustering images 12/20 (60%)",
        "Gallery Cluster: clustering images 13/20 (65%)",
        "Gallery Cluster: clustering images 14/20 (70%)",
        "Gallery Cluster: clustering images 15/20 (75%)",
        "Gallery Cluster: clustering images 16/20 (80%)",
        "Gallery Cluster: clustering images 17/20 (85%)",
        "Gallery Cluster: clustering images 18/20 (90%)",
        "Gallery Cluster: clustering images 19/20 (95%)",
        "Gallery Cluster: clustering images 20/20 (100%)",
    ]


def test_progress_without_a_label_stays_quiet(capsys: pytest.CaptureFixture[str]):
    progress = Progress(3)
    for _ in range(3):
        progress.step()
    assert "\x01i\x02" not in capsys.readouterr().err
