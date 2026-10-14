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

## Releasing

Each plugin has its own version in `plugins/<id>/<id>.yml`. To release a plugin, bump its `version` and push to `main`. The Release workflow then publishes the index to GitHub Pages and tags the plugin as `<id>-v<version>`. A change without a version bump is not released.

GitHub Pages must use GitHub Actions as its source (Settings -> Pages).
