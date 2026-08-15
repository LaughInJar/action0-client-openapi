# Generating a client

One command reads the schema and writes the package:

```shell
action0-openapi petstore.json -o src/
```

```
src/petstore_client/__init__.py
src/petstore_client/client.py
src/petstore_client/errors.py
src/petstore_client/models.py
src/petstore_client/operations.py
src/petstore_client/py.typed
```

The package name (`petstore_client`) and the client class name
(`PetstoreClient`) are derived from the schema's `info.title`; the
client's default base URL comes from the schema's first `servers`
entry (server variables at their defaults). A document without
top-level `servers` falls back to the `servers` declared on its paths
and operations — Open-Meteo's specs are shaped like that — as long as
they all agree on their first URL; several distinct URLs leave the
client without a default (a warning names them), so callers pass
`base_url` themselves. All of it can be overridden:

```shell
action0-openapi petstore.yaml -o src/ \
    --package-name zoo \
    --client-name ZooClient \
    --base-url https://zoo.example.com/v1
```

YAML schemas need the `yaml` extra ({doc}`installation`). Schemas
split over several files (`$ref: './components/geo.yaml#/...'`) are
loaded and bundled automatically — {doc}`schema-support` describes the
merge rules. Existing files are never overwritten unless you pass
`--force`.

The schema can also be an http(s) URL. The URL you name is fetched
directly, but files it *references* download only with your consent —
one `[y/N]` prompt per file, or all of them with `--download`:

```shell
action0-openapi https://example.com/api/openapi.yaml -o src/ --download
```

```
action0-openapi: downloading https://example.com/api/components/geo.yaml
src/example_client/__init__.py
...
```

For large APIs, `--split-by-tag` puts each OpenAPI tag's operations
into a module of its own (`operations_pets.py`, `operations_auth.py`,
...; untagged operations stay in `operations.py`). The package root
re-exports everything either way, so user imports —
`from petstore_client import ListPets` — do not depend on the layout.

Constructs the generator flattens or skips (an unsupported security
scheme, several request media types, ...) are reported
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

from action0.openapi import bundle_documents, generate_package, load_documents
from action0.openapi import parse_api, write_package

document, warnings = bundle_documents(load_documents("petstore.json"))
api = parse_api(document)
files = generate_package(api, client_name="PetstoreClient", schema_name="petstore.json")
write_package(files, Path("src/petstore_client"))
```

(For a schema that is known to be a single file, `load_schema` reads it
without following references, and its result can go straight into
`parse_api`.)
