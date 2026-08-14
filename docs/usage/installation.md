# Installation

Requires Python 3.11 or newer.

```shell
uv add action0-client-openapi
```

or with pip:

```shell
pip install action0-client-openapi
```

JSON schema files work out of the box; to read YAML schema files,
install the `yaml` extra:

```shell
uv add "action0-client-openapi[yaml]"
```

The import name is `action0.openapi`:

```python
import action0.openapi
```

(The package cannot live *inside* `action0.client` — that is a regular,
non-namespace package owned by the `action0-client` distribution — so it
is a sibling in the `action0` namespace instead.)
