import unittest

import requests

from src.utils.model_catalog import ModelCatalogError, fetch_model_ids, models_url


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


class ModelsUrlTest(unittest.TestCase):
    def test_converts_chat_completions_url(self):
        self.assertEqual(
            models_url("https://api.example.com/v1/chat/completions"),
            "https://api.example.com/v1/models",
        )

    def test_accepts_api_root_and_drops_query(self):
        self.assertEqual(
            models_url("https://api.example.com/v1/?token=hidden"),
            "https://api.example.com/v1/models",
        )

    def test_rejects_invalid_url(self):
        with self.assertRaises(ModelCatalogError):
            models_url("localhost:8000/v1")


class FetchModelIdsTest(unittest.TestCase):
    def test_fetches_sorts_and_deduplicates_models(self):
        request = {}

        def fake_get(url, headers, timeout):
            request.update(url=url, headers=headers, timeout=timeout)
            return FakeResponse({"data": [{"id": "z-model"}, {"id": "A-model"}, {"id": "z-model"}]})

        result = fetch_model_ids("https://api.example.com/v1", " secret ", request_get=fake_get)

        self.assertEqual(result, ["A-model", "z-model"])
        self.assertEqual(request["url"], "https://api.example.com/v1/models")
        self.assertEqual(request["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(request["timeout"], 15.0)

    def test_rejects_unexpected_response(self):
        with self.assertRaisesRegex(ModelCatalogError, "缺少 data"):
            fetch_model_ids(
                "https://api.example.com/v1",
                request_get=lambda *args, **kwargs: FakeResponse({"models": []}),
            )


if __name__ == "__main__":
    unittest.main()
