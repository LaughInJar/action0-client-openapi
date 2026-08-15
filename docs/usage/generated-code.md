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
response attached; transport problems arrive as `TransportError` /
`TimeoutError` — see the [action0-client error
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
