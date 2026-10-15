"""Checks every GraphQL document of the plugins against the schema of the Stash release."""

import dataclasses
import importlib.util
from collections.abc import Iterator
from typing import cast

import pytest
from graphql import build_schema, parse, validate
from stash_plugin import Operation

from catalog import PLUGINS_DIR, ROOT

SCHEMA = build_schema((ROOT / "schema" / "stash.graphql").read_text(encoding="utf-8"))


def find_operations(value: object) -> Iterator[Operation[object]]:
    """Operations in a value, including those inside dicts and dataclasses."""
    if isinstance(value, Operation):
        yield value
    elif isinstance(value, dict):
        for item in cast("dict[object, object]", value).values():
            yield from find_operations(item)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        for field in dataclasses.fields(value):
            yield from find_operations(cast(object, getattr(value, field.name)))


def plugin_operations() -> Iterator[tuple[str, Operation[object]]]:
    for api in sorted(PLUGINS_DIR.glob("*/stash_api.py")):
        plugin_id = api.parent.name
        # Each plugin has a module named stash_api, so each gets a unique name here.
        spec = importlib.util.spec_from_file_location(f"{plugin_id}.stash_api", api)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for name, value in cast("dict[str, object]", vars(module)).items():
            for index, operation in enumerate(find_operations(value)):
                yield f"{plugin_id}:{name}[{index}]", operation


OPERATIONS = list(plugin_operations())


def test_every_plugin_has_operations():
    plugins = {name.split(":")[0] for name, _ in OPERATIONS}
    assert plugins == {api.parent.name for api in PLUGINS_DIR.glob("*/stash_api.py")}


@pytest.mark.parametrize("operation", [op for _, op in OPERATIONS], ids=[n for n, _ in OPERATIONS])
def test_operation_matches_the_schema(operation: Operation[object]):
    errors = validate(SCHEMA, parse(operation.document))
    assert not errors, "\n".join(error.message for error in errors)
