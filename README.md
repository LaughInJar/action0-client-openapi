# Action0-Client-OpenAPI

[![CI](https://github.com/LaughInJar/action0-client-openapi/actions/workflows/ci.yml/badge.svg)](https://github.com/LaughInJar/action0-client-openapi/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/action0-client-openapi)](https://pypi.org/project/action0-client-openapi/)

Generate fully typed
[action0-client](https://github.com/LaughInJar/action0-client) API
clients from an OpenAPI schema: one typed operation class per endpoint,
plus the model classes their results are parsed into. The generated code
is plain, readable `action0-client` code — it depends on
`action0-client` only, not on this package — so it runs synchronously,
on asyncio, on Twisted or on an execution model of your own, decided by
the backend you plug in.

```shell
uv add --dev action0-client-openapi     # or: pip install action0-client-openapi
action0-openapi petstore.json -o src/   # YAML schemas: install the "yaml" extra
```

```
src/petstore_client/__init__.py
src/petstore_client/client.py
src/petstore_client/models.py
src/petstore_client/operations.py
src/petstore_client/py.typed
```

Using the generated client — the backend decides the execution model,
and the static types follow it:

```python
from action0.client.backends.requests import RequestsBackend
from petstore_client import GetPet, PetstoreClient

client = PetstoreClient(RequestsBackend(), token="...")
pet = client.send(GetPet(pet_id=42))  # Pet

from action0.client.backends.httpx import AsyncHttpxBackend

client = PetstoreClient(AsyncHttpxBackend(), token="...")
pet = await client.send(GetPet(pet_id=42))  # Awaitable[Pet]
```

Generated code is meant to be checked in and reviewed like hand-written
code: it is readable, ruff-clean and fully typed — mypy strict, pyright
and ty pass on it. Models become plain dataclasses with generated
JSON converters, endpoints become `Operation` subclasses, security
schemes become client constructor credentials — with the schema's
`description`s carried along as docstrings and `#:` doc-comments. The [schema support
matrix](https://laughinjar.github.io/action0-client-openapi/usage/schema-support.html)
lists exactly which OpenAPI 3.0/3.1 constructs are covered — schemas
split over several files are bundled automatically, straight from a URL
too (referenced files download after a per-file confirmation, or with
`--download`) — and what is deliberately deferred (typed multipart
bodies, per-status response typing, ...).

Requires Python 3.11 or newer.

Full documentation including the API reference:
<https://laughinjar.github.io/action0-client-openapi/>

## Development

The project is managed with [uv](https://docs.astral.sh/uv/); `uv run`
syncs the environment automatically:

```shell
uv run pytest          # tests (incl. doctests in the sources)
uv run ruff check      # lint
uv run ruff format     # format
uv run mypy            # type-check (strict)
uv run pyright         # type-check
uv run ty check        # type-check
```

## License

MIT — see [LICENSE](LICENSE).
