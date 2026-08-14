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
| 3.0 `nullable: true`, 3.1 `type: [T, "null"]`, `oneOf`/`anyOf` of one type and `null` | `... \| None` |
| `allOf` with exactly one subschema | unwrapped |
| `allOf` of object schemas (the base-plus-extension inheritance pattern) | flattened into one model: `properties` and `required` united, recursively; properties next to `allOf` count too |

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
| required parameter / property | field without default |
| optional parameter / property | `... \| None = None` (scalar schema `default`s are kept) |
| JSON request body, inline object schema | one `json_field()` per property |
| JSON request body, `$ref`/array/scalar schema | a single `payload: ... = json_body()` field |
| `application/x-www-form-urlencoded` body | one `form_field()` per property (scalars, enums, arrays of those) |
| lowest documented 2xx response with JSON content | the operation's typed result |
| `204` (or 2xx without content) | `Operation[None]` |
| 2xx with only non-JSON content | `Operation[bytes]` |

Operation classes are named after the `operationId` (`listPets` →
`ListPets`), falling back to method + path (`GET /pets/{petId}` →
`GetPetsPetId`).

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

- `oneOf` / `anyOf` unions of several concrete types, and
  `discriminator` polymorphism.
- `allOf` parts that are not object schemas, or that define the same
  property differently — flatten those by hand before generating.
  (Constraint-only keywords inside `allOf` parts, like `minProperties`,
  are ignored.)
- Remote and file `$ref`s (`other.yaml#/...`) — bundle the document
  first; only local `#/...` references resolve.
- Cookie parameters, content-typed parameters, request bodies other
  than JSON and form-urlencoded (e.g. `multipart/form-data`).
- Per-status response typing beyond the picked 2xx (other 2xx responses
  still pass the check and are parsed with the same converter), and
  typed error payloads for 4xx/5xx.
- `properties` combined with `additionalProperties` (the extra keys are
  dropped, with a warning), Swagger 2.0 documents, `callbacks`, `links`
  and `webhooks`.
