# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`action0-client-openapi` is a Python library that generates fully typed [action0-client](https://github.com/LaughInJar/action0-client) API clients from OpenAPI schema files: one typed operation class per endpoint plus the model classes their results are parsed into. The generated code must be plain, readable `action0-client` code that depends on `action0-client` only — not on this package — and therefore runs on any backend (sync, asyncio, Twisted, ...). It ships the `action0.openapi` package (`action0` is a PEP 420 namespace package; the import name is NOT `action0.client.openapi` — `action0.client` is a regular package owned by the `action0-client` distribution, so nothing can nest inside it) from a `src/` layout, is built with hatchling, and uses `uv` for environment/dependency management. The runtime dependency is `action0-client` (from PyPI, which pulls in `action0-req` and `action0-url`); `pyyaml` is an optional extra (`yaml`) for reading YAML schema files and a dev-group dependency.

## Rules

- **Never commit without asking.** Also never push, tag, or publish on your own.
- **Branches + PRs.** All changes go through feature branches and GitHub pull requests that Simon reviews and merges — never commit to `main` directly. (Only the initial project scaffold was built directly on `main`; that phase is over.)
- **Discuss first.** Always present the plan and the intended edits and get agreement before changing files.
- Every code change comes with: tests, docstrings, inline comments where the code isn't self-explanatory, and updated usage examples in `README.md` and the Sphinx docs (the guide pages in `docs/usage/`).
- Before considering work done, run ruff, mypy, pyright, ty, and pytest (commands below) and fix what they report.
- Supported Python versions: 3.11 up to the latest release. Don't use syntax or stdlib features introduced after 3.11, and don't rely on behavior removed in newer versions. This applies to the *generated* code too.

## Commands

`uv run` syncs the environment automatically (the dev dependency group is installed by default), so no separate install step is needed.

```sh
uv run pytest                                          # all tests
uv run pytest tests/action0/openapi/test_init.py       # one file
uv run pytest tests/action0/openapi/test_init.py::PackageTestCase::test_version  # one test

uv run ruff check      # lint (add --fix to autofix)
uv run ruff format     # format
uv run mypy            # type-check (strict; files are configured in pyproject.toml)
uv run pyright         # type-check
uv run ty check        # type-check

uv run action0-openapi tests/action0/openapi/fixtures/petstore.json -o /tmp/generated  # the CLI

uv run --group docs sphinx-build -W --keep-going -b html docs docs/_build/html  # build docs

uv build               # build sdist + wheel into dist/
```

`pytest` also runs the `>>>` examples in the docstrings as doctests (`--doctest-modules` over `src/`), so docstring examples must produce their shown output exactly.

## Architecture

The pipeline under `src/action0/openapi/` is **load → bundle → resolve → parse (→ IR) → render → write**; every stage is a pure function over plain data except `loader` (reads the files) and `generate.write_package` (writes the package). The CLI (`action0-openapi`, `[project.scripts]` → `cli:main`) chains them.

- `errors.py` — `SchemaError`, the one exception for input problems; messages are printed by the CLI as-is, no traceback.
- `loader.py` — `load_schema(path)`: JSON by default, YAML via lazy PyYAML import (missing → "install the yaml extra" message), JSON→YAML fallback for unknown suffixes, OpenAPI 3.0/3.1 validation (Swagger 2.0 gets a dedicated message). `load_documents(source, allow_download=)` additionally follows file `$ref`s (relative to the referencing file, canonical-path/URL keyed) into a `Documents` set; referenced fragment files skip the version check. The source may be an http(s) URL (fetched without asking — the caller named it); *referenced* URLs download only if the `allow_download` callback approves each one (stdlib urllib, 30 s timeout, versioned User-Agent).
- `bundle.py` — `bundle_documents(documents) -> (document, warnings)`, pure: merges a `Documents` set into one single-file document. Refs to another file's `#/components/<section>/<name>` are imported into the root's section under their own name (different definition under a taken name → numbered rename + warning; identical ref-free twin → shared); non-component targets (deep pointers, whole-file refs) are inlined (anonymous cross-file cycles → SchemaError; component cycles are fine). Refs against a URL base resolve via urljoin, so downloaded documents can only reference further URLs, never local files. Single-file documents pass through untouched.
- `resolve.py` — `RefResolver`: local `#/...` JSON pointers only (RFC 6901 unescaping, list indices), `deref()` follows `$ref` chains with cycle detection.
- `ir.py` — the frozen intermediate representation (`Api`, `Model`, `EnumModel`, `Field`, `OperationIR`, `Param`, `Body`, `SecurityScheme`, the `TypeExpr` algebra). All IR names are final Python names; schema spellings survive as `wire_name`/`wire_path`. `Api.warnings` collects flattened/skipped constructs. A future dynamic (import-time) mode would consume this same IR.
- `names.py` — spelling conversion + validity: PascalCase/snake_case/UPPER_SNAKE (acronym runs are one word, pluralized acronyms keep their `s` — `numAPIs` → `num_apis`; accents are transliterated away, not word breaks — `PokéAPI` → `PokeApi`), keyword and reserved-name escaping (trailing underscore; `RESERVED_OPERATION_FIELDS` includes the seven field-specifier names — a field named `query` would shadow the specifier for later fields of the class), digit-led prefixes, `NameRegistry` dedup, path-template rewriting (`{petId}` → `{pet_id}` — `path_param()` has no wire-name parameter, the placeholder must equal the field name).
- `types.py` — leaf type mapping: `type`/`format` → IR scalar, `annotation()`, `imports_for()`, `converter_expr()` (the JSON→typed expression, nested comprehensions with collision-free variables).
- `parse.py` — `parse_api(document) -> Api`. Components first; **model components register their class before their properties are walked** so self references work; inline object/enum schemas are synthesized into named classes via one shared class-name registry (pre-claimed: names generated modules import, like `JsonOperation`). Nullability (3.0 `nullable`, 3.1 type arrays, `oneOf/anyOf` [T, null]) normalizes to one flag. `properties` + `additionalProperties` → the model gets a parse-side catch-all field (`Model.additional_field`, default name `additional_properties`) collecting undeclared payload keys; in a *request body* schema that combination only warns (fields have nowhere to send extras). Parameters without `schema` but with bare `type`/`enum` keywords (Swagger 2.0 style, e.g. Meteomatics) salvage those keywords as their schema with a warning; no `schema`, no bare keywords, or `content` → SchemaError. Bodies: inline object → `json_field()`s, `$ref`/array/scalar → single `payload` `json_body()`, form → `form_field()`s, any other media type → raw `payload` `body()` plus a `content_type` header param preset to the media type. Response: lowest 2xx; JSON → model, none → `Operation[None]`, non-JSON → `Operation[bytes]`. Referenced security schemes → client credentials. Base URL: first top-level server (variables at their defaults); without top-level `servers`, the path/operation-level servers are used when they all agree on one first URL (several distinct URLs → warning, no default).
- `render.py` — code emission as plain string building (no template engine). `Lines`/`Imports` reproduce this repo's ruff format/isort shapes **exactly** (the golden tests enforce byte-stability); over-long lines are wrapped the way ruff format wraps them — conversions (parenthesized ternary one level deeper, comprehensions opened up recursively, each `for`/`if` clause on its own line, an over-long `if x not in y` filter broken before `not in`), `def`/`class` headers, annotated fields (call arguments one line deeper if the head fits, else RHS parenthesized, else the annotation; unsplittable leftovers stay over-long, as ruff leaves them), and set constants (one element per line + magic trailing comma — a catch-all model's `_<MODEL>_PROPERTIES` declared-keys set, emitted above its converter). The `longnames.json` fixture pins every wrap shape (`tests/.../test_render.py::WrappingTestCase`, incl. a ruff format/check subprocess run). `render_models` / `render_operations` / `render_client` / `render_init`. UUID fields get `serialize=str` (the one type action0-client's serializers reject); apiKey-in-query auth becomes a `prepare()` override.
- `generate.py` — `generate_package` (all files incl. `py.typed`, versioned do-not-edit header; `split_by_tag=` groups operations into `operations_<tag>.py` modules, untagged ones staying in `operations.py`, with the package root re-exporting everything so user imports are layout-independent), `write_package` (refuses to overwrite without force), `default_package_name`/`default_client_name`.
- `cli.py` — stdlib argparse; exit 0/1; warnings → stderr, written files → stdout. The schema argument may be an http(s) URL; referenced downloads get one `[y/N]` prompt each on stderr (no TTY → error suggesting `--download`; `--download` approves all and announces each URL).

Generated code targets the `action0-client` public API (`Operation`/`JsonOperation`, the field specifiers, `APIClient[BackendT_co]`) and must itself pass ruff format/check, mypy strict, pyright and ty. `examples/petstore.py` in `../action0-client` is the canonical output shape; study its `CLAUDE.md` before changing emitter output.

Testing setup worth knowing:

- `tests/action0/openapi/fixtures/petstore.json` (+ `.yaml` twin) exercises the whole supported subset; `tests/action0/openapi/golden/petstore_client/` is the package generated from it, checked in. The golden package is pinned four ways: byte-comparison (`test_generate.py`, generator version normalized away), pytest import (runs `__init_subclass__` validation of every generated operation), the repository-wide mypy/pyright/ty runs (it lives under `tests/`), and `ruff format --check`/`ruff check` run from `test_render.py`. When emitter output changes intentionally, regenerate the golden package (`write_package(generate_package(...), GOLDEN, force=True)`) and review the diff.
- `test_e2e.py` imports the golden package and drives it through `action0.client.testing.StubBackend`, asserting both wire directions.

Conventions:

- The version is single-sourced as `__version__` in `src/action0/openapi/__init__.py`; hatch extracts it with the regex in `[tool.hatch.version]`. Bump it only there.
- Releases: pushing a `vX.Y.Z` tag triggers `.github/workflows/release.yml`, which re-runs all checks, verifies the tag matches `__version__`, builds, and publishes to PyPI via trusted publishing (environment `pypi`). Never bump the version, tag, or publish on your own — releasing is the user's call.
- Tests mirror the `src/` layout under `tests/action0/openapi/` and are `unittest.TestCase` classes, executed via pytest.
- Ruff enforces one import per line (isort `force-single-line`), line length 99, `action0` as first-party.
- Docs live in `docs/` (Sphinx + Furo, MyST Markdown pages, autodoc for the API reference). Docstrings are Sphinx-reST (`:param:`, `:py:meth:` roles). CI builds them with `-W` on every run and deploys to GitHub Pages on pushes to `main`. Guide examples in `docs/usage/` (one page per topic, stitched together by `docs/usage/index.md`) show exact outputs in `#` comments — keep them truthful.
