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
the backend you plug in:

```python
client = APIClient(RequestsBackend(), "https://api.example.com/v1")
pet = client.send(GetPetById(pet_id=42))  # Pet

client = APIClient(AsyncHttpxBackend(), "https://api.example.com/v1")
pet = await client.send(GetPetById(pet_id=42))  # Awaitable[Pet]
```

(`GetPetById` and `Pet` are generated from the schema's
`getPetById` operation and `Pet` component.)

Requires Python 3.11 or newer.

Full documentation including the API reference:
<https://laughinjar.github.io/action0-client-openapi/>

**Status:** early scaffold — the generator design and implementation are
in progress; nothing is generated yet.

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
