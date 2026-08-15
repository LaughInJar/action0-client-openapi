# Supported schemas

The generator understands OpenAPI **3.0.x and 3.1.x** documents, as JSON
or — with the `yaml` extra installed — YAML. This page lists how each
construct maps to generated code, and what is (deliberately) out of
scope for now. Everything unsupported raises a clear error naming the
schema location; lesser omissions are reported as warnings.

## Types

| Schema | Generated Python |
|---|---|
| `type: string` / `integer` / `number` / `boolean` | `str` / `int` / `float` / `bool` |
| `format: date` / `date-time` | `datetime.date` / `datetime.datetime` (ISO on the wire) |
| `format: uuid` | `uuid.UUID` |
| other string formats (`email`, `byte`, `binary`, ...) | `str` |
| `enum` (pure string or pure integer values) | a generated `enum.Enum` subclass |
| `type: array` | `list[...]` (missing `items` means `list[Any]`) |
| object with `properties` | a generated dataclass model |
| object with only `additionalProperties: <schema>` | `dict[str, ...]` |
| object without properties, or an empty schema | `dict[str, Any]` / `Any` |
| unrecognized `type` (e.g. `""` in broken generated specs) | `Any`, with a warning |
| 3.0 `nullable: true`, 3.1 `type: [T, "null"]`, `oneOf`/`anyOf` of one type and `null` | `... \| None` |
| `oneOf` / `anyOf` of several types | a generated union: a type alias (`Companion: TypeAlias = "Cat \| Dog"`) plus a dispatching converter |
| `discriminator` (with or without `mapping`) | tag dispatch in the union's converter; members without a mapping entry use their component name, the spec's implicit convention |
| 3.1 multi-type arrays (`type: ["string", "integer"]`) | a union of the bare types |
| `allOf` with exactly one subschema | unwrapped |
| `allOf` of object schemas (the base-plus-extension inheritance pattern) | flattened into one model: `properties` and `required` united, recursively; properties next to `allOf` count too; when subschemas define the same property differently, the later definition wins (the base-then-specialization idiom), with a warning |
| `description` on a component | the class docstring (a `#:` doc-comment on a union's type alias) |
| `description` on a property | a `#:` doc-comment above the dataclass field (Sphinx autodoc reads those); looked up through a `$ref` if the property is one |

Model fields keep the schema's property order, except that fields
rendered with a `= None` default — optional or nullable ones — move
behind the default-less fields, as plain dataclasses require. A
required-but-nullable property therefore reads `... | None = None`,
but its converter still expects the key in the payload.

Union members must be recognizable in a decoded payload — by JSON type
(a `string \| object` union), by the `discriminator` tag, or by a
required property no other member declares. A union whose members
cannot be told apart (two plain-string members, object members with
only shared optional properties, a member accepting anything) degrades
to an untyped value, with a warning naming the schema location — add a
`discriminator` to fix that.

Inline object and enum schemas are synthesized into named classes: an
inline response object of `createToken` becomes `CreateTokenResponse`,
an inline enum of a `status` property of `Pet` becomes `PetStatus`.
Names are converted to PEP 8 (`petId` → `pet_id`, classes `PascalCase`,
enum members `UPPER_SNAKE`); the original spelling is kept as the wire
name. Python keywords and names reserved by
[action0-client](https://laughinjar.github.io/action0-client/)
operations get a trailing underscore.

## Operations

| Schema | Generated Python |
|---|---|
| path / query / header parameters | `path_param()` / `query()` / `header()` fields |
| parameter with `type`/`enum` directly on the parameter instead of under `schema:` (a Swagger 2.0 habit some specs keep) | those keywords are treated as the parameter's schema, with a warning |
| required parameter / property | field without default |
| optional parameter / property | `... \| None = None` (scalar schema `default`s are kept) |
| JSON request body, inline object schema | one `json_field()` per property |
| JSON request body, `$ref`/array/scalar schema | a single `payload: ... = json_body()` field |
| `application/x-www-form-urlencoded` body | one `form_field()` per property (scalars, enums, arrays of those) |
| any other request media type (file uploads: `application/octet-stream`, `image/*`, ...) | a raw `payload: bytes = body()` field, plus a `content_type` header field preset to the media type (several raw media types: the first is sent, with a warning) |
| lowest documented 2xx response with JSON content | the operation's typed result (a result needing no conversion is returned through `typing.cast` so mypy strict accepts the generated `load_json`) |
| `204` (or 2xx without content) | `Operation[None]` |
| 2xx with only non-JSON content | `Operation[bytes]` |
| operation `summary` / `description` | the operation class docstring |
| `description` on a parameter or body property | a `#:` doc-comment above the field |

Operation classes are named after the `operationId` (`listPets` →
`ListPets`), falling back to method + path (`GET /pets/{petId}` →
`GetPetsPetId`).

## Multi-file documents

Schemas split over several files — references like
`$ref: './components/geo.yaml#/components/schemas/Point'`, with paths
relative to the referencing file — are bundled into one document before
translation:

- A reference to another file's `#/components/<section>/<name>` moves
  that component (and, recursively, everything it references) into the
  root document's matching section. It keeps its name; if the name is
  already taken by a *different* definition, a numbered name is picked
  (`Tag` → `Tag2`) and a warning reports the rename. An identical,
  reference-free definition is shared silently instead.
- A reference to anything that is not a component — a deep pointer like
  `other.yaml#/components/schemas/Pet/properties/name`, or a whole-file
  reference to a bare schema file — is inlined in place, like an inline
  schema written there. A reference *cycle* through such anonymous
  nodes cannot be inlined and is an error; cycles through components
  are fine.

The schema itself may be an `http(s)://` URL, and references may point
at URLs too — relative references in a downloaded document resolve
against its URL, which also means a downloaded document can only ever
reference further URLs, never files on your disk. You named the root
URL, so it is fetched directly; referenced files you did *not* name
download only with consent: the CLI asks `download <url>? [y/N]` per
file (`--download` pre-approves all of them), the library API takes an
`allow_download` callback.

## Security schemes

Schemes referenced by the document's or any operation's `security` become
constructor credentials of the generated client:

| Scheme | Client credential |
|---|---|
| `http` / `bearer` | `token` (an `Authorization: Bearer ...` default header) |
| `http` / `basic` | `username` + `password` |
| `apiKey` in `header` | a header credential parameter |
| `apiKey` in `query` | a query credential added to every request |

OAuth2 and OpenID Connect flows are not generated — a warning tells you
to pass those credentials yourself (default headers or an
`APIClient.prepare` override).

## Not supported (yet)

- `allOf` parts that are not object schemas — flatten those by hand
  before generating. (Constraint-only keywords inside `allOf` parts,
  like `minProperties`, are ignored.)
- Cookie parameters, content-typed parameters, and *typed* multipart
  bodies — `multipart/form-data` gets the raw-bytes treatment, so you
  assemble the multipart payload yourself.
- Per-status response typing beyond the picked 2xx (other 2xx responses
  still pass the check and are parsed with the same converter), and
  typed error payloads for 4xx/5xx.
- `properties` combined with `additionalProperties` (the extra keys are
  dropped, with a warning), Swagger 2.0 documents, `callbacks`, `links`
  and `webhooks`.
