# action0-client-openapi

Generate fully typed
[action0-client](https://laughinjar.github.io/action0-client/) API
clients from an OpenAPI schema: one typed operation class per endpoint,
plus the model classes their results are parsed into. The generated code
is plain, readable `action0-client` code — it depends on
`action0-client` only, not on this package — so it runs synchronously,
on asyncio, on Twisted or on an execution model of your own, decided by
the backend you plug in.

**Status:** early scaffold — the generator design and implementation are
in progress; nothing is generated yet.

The `action0` namespace is simply the one the author likes to use for
personal projects.

```{toctree}
:maxdepth: 2

usage/index
api
```
