# Contributing

You need [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run ruff format --check   # formatting, `uv run ruff format` fixes it
uv run ruff check            # lint
uv run basedpyright          # types
uv run pytest
uv run python scripts/build_site.py   # builds the plugin index into _site
uv run python scripts/download_schema.py [tag]   # downloads the Stash GraphQL schema into schema/
```

## Layout

Each plugin lives in `plugins/<id>/`, with its manifest `<id>.yml` and the `main.py` that Stash runs. `shared/stash_plugin.py` holds what every plugin needs: reading Stash's input, logging, and the GraphQL client. The build copies it into each plugin's zip, so to try a plugin in Stash, install it from the zip that `scripts/build_site.py` builds rather than from its directory.

GraphQL documents are `Operation` constants whose type parameter is the shape of the data they return. `tests/test_graphql.py` checks every document against `schema/stash.graphql`, so update the schema when targeting a new Stash release.

## Releasing

Each plugin has its own version in `plugins/<id>/<id>.yml`. To release a plugin, bump its `version` and push to `main`. The Release workflow then publishes the index to GitHub Pages and tags the plugin as `<id>-v<version>`. A change without a version bump is not released, and that includes changes to `shared/`: bump each plugin that should ship them.

GitHub Pages must use GitHub Actions as its source (Settings -> Pages).
