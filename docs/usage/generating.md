# Generating a client

One command reads the schema and writes the package:

```shell
action0-openapi petstore.json -o src/
```

```
src/petstore_client/__init__.py
src/petstore_client/client.py
src/petstore_client/models.py
src/petstore_client/operations.py
src/petstore_client/py.typed
```

The package name (`petstore_client`) and the client class name
(`PetstoreClient`) are derived from the schema's `info.title`; the
client's default base URL comes from the schema's first `servers`
entry. All of it can be overridden:

```shell
action0-openapi petstore.yaml -o src/ \
    --package-name zoo \
    --client-name ZooClient \
    --base-url https://zoo.example.com/v1
```

YAML schemas need the `yaml` extra ({doc}`installation`). Existing
files are never overwritten unless you pass `--force`.

For large APIs, `--split-by-tag` puts each OpenAPI tag's operations
into a module of its own (`operations_pets.py`, `operations_auth.py`,
...; untagged operations stay in `operations.py`). The package root
re-exports everything either way, so user imports —
`from petstore_client import ListPets` — do not depend on the layout.

Constructs the generator flattens or skips (an unsupported security
scheme, `additionalProperties` next to `properties`, ...) are reported
as warnings on stderr; constructs outside the supported subset stop the
run with an error naming the schema location — {doc}`schema-support`
lists both categories.

The generated code is meant to be **checked in** like hand-written
code: it is readable, fully typed (mypy strict, pyright and ty pass on
it), ruff-clean, and it depends only on
[action0-client](https://laughinjar.github.io/action0-client/) — not on
this package. Regenerate with `--force` when the schema changes, and
review the diff like any other change.

The same pipeline is available as a library — one function per stage:

```python
from pathlib import Path

from action0.openapi import generate_package, load_schema, parse_api, write_package

api = parse_api(load_schema("petstore.json"))
files = generate_package(api, client_name="PetstoreClient", schema_name="petstore.json")
write_package(files, Path("src/petstore_client"))
```
