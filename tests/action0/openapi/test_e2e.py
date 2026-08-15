"""
End-to-end: the checked-in golden package, driven through action0-client.

These tests import the golden petstore package like a user would and
send its operations through :py:class:`action0.client.testing.StubBackend`,
asserting both directions of the wire: the requests that leave (method,
URL, query, headers, body) and the typed results that come back.
"""

import datetime
import importlib
import json
import sys
import unittest
from pathlib import Path
from typing import Any

from action0.client import APIError
from action0.client.testing import StubBackend
from action0.req import Response

GOLDEN_PARENT = Path(__file__).parent / "golden"


def json_response(status: int, payload: Any) -> Response:
    """
    Build a canned JSON response.

    :param status: the HTTP status
    :param payload: the JSON payload
    :return: the response
    """
    return Response(
        status,
        headers={"Content-Type": "application/json"},
        body=json.dumps(payload).encode(),
    )


class EndToEndTestCase(unittest.TestCase):
    """
    tests driving the golden petstore client against a stub backend
    """

    petstore: Any

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(GOLDEN_PARENT))
        cls.addClassCleanup(sys.path.remove, str(GOLDEN_PARENT))
        cls.petstore = importlib.import_module("petstore_client")

    def client(self, *responses: Response) -> Any:
        """
        Build a golden client over a stub backend.

        :param responses: the canned responses
        :return: the client
        """
        return self.petstore.PetstoreClient(StubBackend(*responses), "secret", "k123")

    def test_query_and_headers(self) -> None:
        """
        Test ListPets: URL, repeated query params, auth and accept
        headers, and the parsed list of models.
        """
        payload = [{"id": 1, "name": "Rex", "status": "on-sale", "bornOn": "2020-05-04"}]
        client = self.client(json_response(200, payload))
        pets = client.send(self.petstore.ListPets(limit=5, tags=["dog", "good"]))
        request = client.backend.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.url.path, "/v1/pets")
        # tags is declared explode: false — the items join into one pair
        self.assertEqual(request.url.query.get_all("tags"), ["dog,good"])
        self.assertEqual(request.url.query.get("limit"), "5")
        # the client's prepare() adds the query credential
        self.assertEqual(request.url.query.get("api_key"), "k123")
        self.assertEqual(request.headers.get("Authorization"), "Bearer secret")
        self.assertEqual(request.headers.get("Accept"), "application/json")
        self.assertEqual(pets[0].name, "Rex")
        self.assertEqual(pets[0].status, self.petstore.PetStatus.ON_SALE)
        self.assertEqual(pets[0].born_on, datetime.date(2020, 5, 4))

    def test_json_field_body(self) -> None:
        """
        Test CreatePet: the inline body becomes a JSON object, None
        fields are omitted.
        """
        client = self.client(json_response(201, {"id": 7, "name": "Bello"}))
        pet = client.send(self.petstore.CreatePet(name="Bello"))
        request = client.backend.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers.get("Content-Type"), "application/json")
        self.assertEqual(json.loads(request.body_str()), {"name": "Bello"})
        self.assertEqual(pet.id, 7)

    def test_json_body_payload(self) -> None:
        """
        Test ReplacePet: a model dataclass serializes as the whole body,
        None fields dropped, enums and dates as wire values.
        """
        pet = self.petstore.Pet(
            id=7, name="Bello", status=self.petstore.PetStatus.SOLD, born_on=None
        )
        client = self.client(json_response(200, {"id": 7, "name": "Bello", "status": "sold"}))
        client.send(self.petstore.ReplacePet(pet_id=7, payload=pet))
        request = client.backend.requests[0]
        self.assertEqual(request.url.path, "/v1/pets/7")
        self.assertEqual(
            json.loads(request.body_str()), {"id": 7, "name": "Bello", "status": "sold"}
        )

    def test_form_body(self) -> None:
        """
        Test CreateToken: form fields become an urlencoded body.
        """
        client = self.client(json_response(200, {"access_token": "at"}))
        token = client.send(self.petstore.CreateToken(grant_type="client_credentials"))
        request = client.backend.requests[0]
        self.assertEqual(request.headers.get("Content-Type"), "application/x-www-form-urlencoded")
        self.assertEqual(request.body_str(), "grant_type=client_credentials")
        self.assertEqual(token.access_token, "at")

    def test_raw_body(self) -> None:
        """
        Test UploadPetPhoto: the bytes payload goes out verbatim with
        the preset Content-Type header, which stays overridable.
        """
        client = self.client(Response(204), Response(204))
        self.assertIsNone(client.send(self.petstore.UploadPetPhoto(pet_id=7, payload=b"\x89PNG")))
        request = client.backend.requests[0]
        self.assertEqual(request.method, "PUT")
        self.assertEqual(request.url.path, "/v1/pets/7/photo")
        self.assertEqual(request.headers.get("Content-Type"), "image/png")
        self.assertEqual(request.body_bytes(), b"\x89PNG")
        client.send(
            self.petstore.UploadPetPhoto(pet_id=7, payload=b"BM6", content_type="image/bmp")
        )
        self.assertEqual(client.backend.requests[1].headers.get("Content-Type"), "image/bmp")

    def test_no_content(self) -> None:
        """
        Test DeletePet: 204 parses into None.
        """
        client = self.client(Response(204))
        self.assertIsNone(client.send(self.petstore.DeletePet(pet_id=7)))
        self.assertEqual(client.backend.requests[0].method, "DELETE")

    def test_bytes_response(self) -> None:
        """
        Test GetPetPhoto: the raw body comes back as bytes.
        """
        client = self.client(Response(200, body=b"\x89PNG"))
        self.assertEqual(client.send(self.petstore.GetPetPhoto(pet_id=7)), b"\x89PNG")

    def test_untyped_json_response(self) -> None:
        """
        Test GetInventory: a JSON result needing no conversion comes
        back as the decoded payload.
        """
        client = self.client(json_response(200, {"on-sale": 3, "sold": 7}))
        self.assertEqual(client.send(self.petstore.GetInventory()), {"on-sale": 3, "sold": 7})

    def test_documented_error_raises_typed_exception(self) -> None:
        """
        Test that a documented error status raises the generated
        exception carrying the parsed error model (still an APIError).
        """
        client = self.client(json_response(404, {"code": 404, "message": "no such pet"}))
        with self.assertRaises(self.petstore.NotFoundError) as caught:
            client.send(self.petstore.GetPet(pet_id=999))
        self.assertIsInstance(caught.exception, APIError)
        error = caught.exception.error
        self.assertIsInstance(error, self.petstore.Error)
        self.assertEqual(error.message, "no such pet")
        self.assertEqual(error.code, 404)
        response = caught.exception.response
        assert response is not None
        self.assertEqual(response.status, 404)

    def test_default_error_response_covers_any_status(self) -> None:
        """
        Test that a ``default`` error response catches every non-2xx
        status of its operation.
        """
        client = self.client(json_response(503, {"code": 1, "message": "down"}))
        with self.assertRaises(self.petstore.DefaultError) as caught:
            client.send(self.petstore.ListPets())
        self.assertEqual(caught.exception.error.message, "down")

    def test_unparsable_error_body_falls_back_to_plain_apierror(self) -> None:
        """
        Test that a documented status with a non-JSON body still raises,
        as the plain APIError.
        """
        client = self.client(Response(404, body=b"<html>gone</html>"))
        with self.assertRaises(APIError) as caught:
            client.send(self.petstore.GetPet(pet_id=999))
        self.assertNotIsInstance(caught.exception, self.petstore.NotFoundError)

    def test_undocumented_error_status_raises_plain_apierror(self) -> None:
        """
        Test that a status without a documented error response keeps
        the base behavior (GetPet documents only 404).
        """
        client = self.client(json_response(500, {"code": 1, "message": "boom"}))
        with self.assertRaises(APIError) as caught:
            client.send(self.petstore.GetPet(pet_id=999))
        self.assertNotIsInstance(caught.exception, self.petstore.NotFoundError)
