# The generated code

A tour of the package generated from the petstore schema (the exact
package this repository pins in its tests). Everything is plain
[action0-client](https://laughinjar.github.io/action0-client/) code —
if you have hand-written operations before, there is nothing new to
learn.

## Models — `models.py`

Schema components become dataclasses; enums become `enum.Enum`
subclasses; each model gets a module-level converter function building
it from a decoded JSON payload (required keys via `data[...]`, optional
ones via `data.get(...)`, dates/UUIDs/enums/nested models converted).
The schema's human-readable text survives: a component's `description`
becomes the class docstring, a property's `description` becomes a `#:`
doc-comment above its field (which Sphinx autodoc reads as the
attribute's documentation):

```python
@dataclass
class Pet:
    """One pet of the store."""

    #: The pet's unique identifier.
    id: int
    name: str
    #: The pet's sale status.
    status: PetStatus | None = None
    #: The pet's day of birth.
    #: Unknown for pets rescued from the wild.
    born_on: datetime.date | None = None  # "bornOn" on the wire
    friends: list[Pet] | None = None


def pet_from_json(data: Any) -> Pet: ...
```

A schema combining `properties` with `additionalProperties` gets a
*catch-all* field: the converter collects every payload key outside the
declared properties into a dict typed after the `additionalProperties`
schema — APIs answering with dynamic keys (Open-Meteo's ensemble member
variables, for example) stay fully typed:

```python
@dataclass
class HealthRecord:
    """One veterinary examination of a pet."""

    clinic: str
    #: The day of the examination. Null when it is not recorded.
    examined_on: datetime.date | None = None
    notes: str | None = None
    #: Measured values by name (weight, temperature, ...).
    additional_properties: dict[str, float] | None = None


# payload keys outside this set land in additional_properties
_HEALTH_RECORD_PROPERTIES = {"clinic", "examinedOn", "notes"}


def health_record_from_json(data: Any) -> HealthRecord: ...
```

The catch-all is a parse-side feature: serialized into a JSON request
body, the model would send it as a nested `additional_properties`
object, not flattened — leave it `None` there.

## Operations — `operations.py`

One class per endpoint, method and path fixed per class, parameters and
body as typed fields (wire spellings preserved via the specifiers'
`name` argument), the response parsed into the model. The operation's
`summary` and `description` form the class docstring; parameter and
body-property `description`s become `#:` doc-comments above their
fields:

```python
class GetPet(JsonOperation[Pet]):
    """``GET /pets/{petId}``"""

    method = Method.GET
    path = "/pets/{pet_id}"

    #: The identifier of the pet to operate on.
    pet_id: int = path_param()

    def load_json(self, data: Any) -> Pet:
        return pet_from_json(data)
```

A `204` endpoint subclasses `Operation[None]`, a non-JSON response
`Operation[bytes]`. A JSON result that needs no conversion — a plain
scalar, or an object without typed properties like an
`additionalProperties: integer` inventory — is returned through
`typing.cast`, since handing the decoded `Any` back as-is would fail
mypy strict:

```python
class GetInventory(JsonOperation[dict[str, int]]):
    """``GET /store/inventory`` — Returns pet quantities by status."""

    method = Method.GET
    path = "/store/inventory"

    def load_json(self, data: Any) -> dict[str, int]:
        return cast(dict[str, int], data)
```

## Error responses — `errors.py`

Documented 4xx/5xx (or `4XX`/`5XX`/`default`) responses with a JSON
object schema become typed exceptions: one
`action0.client.APIError` subclass per (status, error model) pair,
named after the status, carrying the parsed payload as `.error`. The
operation overrides `check` to raise them; an error body that is not a
JSON object falls through to the plain `APIError`:

```python
class NotFoundError(APIError):
    """Raised for the documented ``404`` answer, parsed into a :py:class:`Error`."""

    def __init__(self, message: str, *, response: Response, error: Error) -> None: ...
```

```python
class GetPet(JsonOperation[Pet]):
    ...

    def check(self, response: Response) -> None:
        if response.status == 404:
            data = decode_error(response)
            if isinstance(data, dict):
                message = f"{type(self).__name__}: unexpected status {response.status}"
                raise NotFoundError(
                    f"{message} {response.phrase}".rstrip(),
                    response=response,
                    error=error_from_json(data),
                )
        super().check(response)
```

## The client — `client.py`

The security schemes become constructor credentials; an
apiKey-in-query scheme is added to every request in `prepare()`:

```python
class PetstoreClient(APIClient[BackendT_co]):
    """The Petstore API client."""

    def __init__(
        self,
        backend: BackendT_co,
        token: str,
        api_key_auth: str,
        base_url: str = "https://petstore.example.com/v1",
    ) -> None: ...
```

## Using it

The client stays generic over the backend, so the execution model is
your choice — sync, asyncio or Twisted, with the static types
following along:

```python
from action0.client.backends.requests import RequestsBackend
from petstore_client import GetPet, PetstoreClient

client = PetstoreClient(RequestsBackend(), token="...", api_key_auth="...")
pet = client.send(GetPet(pet_id=42))  # Pet

from action0.client.backends.httpx import AsyncHttpxBackend

client = PetstoreClient(AsyncHttpxBackend(), token="...", api_key_auth="...")
pet = await client.send(GetPet(pet_id=42))  # Awaitable[Pet]
```

Non-2xx responses raise `action0.client.APIError` with the request and
response attached — a *documented* error status raises the generated
subclass from `errors.py` with the parsed payload on top:

```python
from petstore_client import NotFoundError

try:
    pet = client.send(GetPet(pet_id=999))
except NotFoundError as error:  # the documented 404, parsed
    print(error.error.code, error.error.message)
```

Transport problems arrive as `TransportError` / `TimeoutError` — see
the [action0-client error
guide](https://laughinjar.github.io/action0-client/usage/errors.html).

## Testing your integration

The stub backends of `action0.client.testing` drive generated clients
without a server, exactly like the tests of this repository drive the
pinned petstore package:

```python
from action0.client.testing import StubBackend
from action0.req import Response

backend = StubBackend(
    Response(200, headers={"Content-Type": "application/json"}, body=b'{"id": 1, "name": "Rex"}')
)
client = PetstoreClient(backend, token="t", api_key_auth="k")

pet = client.send(GetPet(pet_id=1))
print(pet.name)  # Rex
print(backend.requests[0].url.path)  # /v1/pets/1
```
