# action0-client-openapi

Generate fully typed
[action0-client](https://laughinjar.github.io/action0-client/) API
clients from an OpenAPI schema: one typed operation class per endpoint,
plus the model classes their results are parsed into. The generated code
is plain, readable `action0-client` code — it depends on
`action0-client` only, not on this package — so it runs synchronously,
on asyncio, on Twisted or on an execution model of your own, decided by
the backend you plug in.

```shell
action0-openapi petstore.json -o src/
```

```python
from petstore_client import GetPet, PetstoreClient

client = PetstoreClient(RequestsBackend(), token="...")
pet = client.send(GetPet(pet_id=42))  # Pet

client = PetstoreClient(AsyncHttpxBackend(), token="...")
pet = await client.send(GetPet(pet_id=42))  # Awaitable[Pet]
```

The generated code is meant to be checked in and reviewed like
hand-written code: readable, ruff-clean, and fully typed — mypy strict,
pyright and ty pass on it. {doc}`usage/schema-support` lists exactly
which OpenAPI constructs are covered.

The `action0` namespace is simply the one the author likes to use for
personal projects.

```{toctree}
:maxdepth: 2

usage/index
api
```
