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
        self.assertEqual(request.url.query.get_all("tags"), ["dog", "good"])
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

    def test_error_status_raises(self) -> None:
        """
        Test that non-2xx statuses raise APIError with the response.
        """
        client = self.client(json_response(404, {"message": "no such pet"}))
        with self.assertRaises(APIError) as caught:
            client.send(self.petstore.GetPet(pet_id=999))
        response = caught.exception.response
        assert response is not None
        self.assertEqual(response.status, 404)
