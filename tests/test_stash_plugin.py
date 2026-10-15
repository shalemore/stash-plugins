import pytest
from stash_plugin import HookContext, PluginInput, StashClient, as_bool, emit_warn, graphql_url


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
