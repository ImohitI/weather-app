import json
import pytest
from unittest.mock import MagicMock, patch
import requests

import app as app_module
from app import app, build_prompt, PROVIDER_MODELS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


MOCK_WEATHER = {
    "name": "London",
    "sys": {"country": "GB"},
    "main": {"temp": 15.0, "feels_like": 13.5, "humidity": 72},
    "wind": {"speed": 4.2},
    "weather": [{"description": "light rain", "icon": "10d"}],
}

MOCK_LLM_RESPONSE = MagicMock()
MOCK_LLM_RESPONSE.choices[0].message.content = "It's a rainy day in London. Grab a jacket!"


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_contains_city(self):
        prompt = build_prompt(MOCK_WEATHER, "London")
        assert "London" in prompt

    def test_contains_temperature(self):
        prompt = build_prompt(MOCK_WEATHER, "London")
        assert "15.0" in prompt

    def test_contains_humidity(self):
        prompt = build_prompt(MOCK_WEATHER, "London")
        assert "72" in prompt

    def test_contains_wind(self):
        prompt = build_prompt(MOCK_WEATHER, "London")
        assert "4.2" in prompt

    def test_contains_condition(self):
        prompt = build_prompt(MOCK_WEATHER, "London")
        assert "light rain" in prompt


# ---------------------------------------------------------------------------
# GET /api/models
# ---------------------------------------------------------------------------

class TestGetModels:
    def test_returns_200(self, client):
        resp = client.get("/api/models")
        assert resp.status_code == 200

    def test_all_providers_present(self, client):
        data = resp_json(client.get("/api/models"))
        for provider in PROVIDER_MODELS:
            assert provider in data

    def test_each_provider_has_models(self, client):
        data = resp_json(client.get("/api/models"))
        for provider, models in data.items():
            assert len(models) > 0

    def test_model_shape(self, client):
        data = resp_json(client.get("/api/models"))
        for models in data.values():
            for m in models:
                assert "id" in m
                assert "label" in m


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

class TestIndex:
    def test_returns_200(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_returns_html(self, client):
        resp = client.get("/")
        assert b"<!DOCTYPE html>" in resp.data


# ---------------------------------------------------------------------------
# POST /api/weather — input validation
# ---------------------------------------------------------------------------

class TestWeatherValidation:
    def test_missing_city_returns_400(self, client):
        resp = client.post("/api/weather", json={"provider": "groq"})
        assert resp.status_code == 400
        assert "City name is required" in resp_json(resp)["error"]

    def test_empty_city_returns_400(self, client):
        resp = client.post("/api/weather", json={"city": "  ", "provider": "groq"})
        assert resp.status_code == 400

    def test_unknown_provider_returns_400(self, client):
        resp = client.post("/api/weather", json={"city": "London", "provider": "fakeai"})
        assert resp.status_code == 400
        assert "fakeai" in resp_json(resp)["error"]

    def test_no_body_returns_400(self, client):
        resp = client.post("/api/weather", content_type="application/json", data="")
        assert resp.status_code == 400

    @patch.object(app_module, "OPENWEATHER_API_KEY", None)
    def test_missing_weather_key_returns_500(self, client):
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert resp.status_code == 500
        assert "OPENWEATHER_API_KEY" in resp_json(resp)["error"]


# ---------------------------------------------------------------------------
# POST /api/weather — happy path
# ---------------------------------------------------------------------------

class TestWeatherHappyPath:
    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_returns_200(self, _weather, _llm, client):
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert resp.status_code == 200

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_response_fields(self, _weather, _llm, client):
        data = resp_json(client.post("/api/weather", json={"city": "London", "provider": "groq"}))
        for field in ("city", "country", "temp", "feels_like", "humidity", "wind_speed",
                      "description", "icon_code", "summary", "provider", "model_id"):
            assert field in data, f"Missing field: {field}"

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_correct_city_and_country(self, _weather, _llm, client):
        data = resp_json(client.post("/api/weather", json={"city": "London", "provider": "groq"}))
        assert data["city"] == "London"
        assert data["country"] == "GB"

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_summary_from_llm(self, _weather, _llm, client):
        data = resp_json(client.post("/api/weather", json={"city": "London", "provider": "groq"}))
        assert data["summary"] == "It's a rainy day in London. Grab a jacket!"

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_provider_echoed(self, _weather, _llm, client):
        data = resp_json(client.post("/api/weather", json={"city": "London", "provider": "groq"}))
        assert data["provider"] == "groq"

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_uses_default_model_when_invalid(self, _weather, _llm, client):
        data = resp_json(client.post("/api/weather", json={
            "city": "London", "provider": "groq", "model": "groq/nonexistent-model"
        }))
        assert data["model_id"] == PROVIDER_MODELS["groq"]["default"]

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_uses_specified_model(self, _weather, _llm, client):
        model = "groq/llama-3.1-8b-instant"
        data = resp_json(client.post("/api/weather", json={
            "city": "London", "provider": "groq", "model": model
        }))
        assert data["model_id"] == model

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_temp_is_rounded(self, _weather, _llm, client):
        data = resp_json(client.post("/api/weather", json={"city": "London", "provider": "groq"}))
        assert data["temp"] == 15.0
        assert data["feels_like"] == 13.5

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_description_is_title_case(self, _weather, _llm, client):
        data = resp_json(client.post("/api/weather", json={"city": "London", "provider": "groq"}))
        assert data["description"] == "Light Rain"


# ---------------------------------------------------------------------------
# POST /api/weather — weather service errors
# ---------------------------------------------------------------------------

class TestWeatherServiceErrors:
    def _http_error(self, status_code):
        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        err = requests.HTTPError(response=mock_resp)
        return err

    @patch("app.fetch_weather", side_effect=lambda city: (_ for _ in ()).throw(
        requests.HTTPError(response=MagicMock(status_code=404))))
    def test_city_not_found_returns_404(self, _mock, client):
        resp = client.post("/api/weather", json={"city": "Nowhere", "provider": "groq"})
        assert resp.status_code == 404
        assert "not found" in resp_json(resp)["error"]

    @patch("app.fetch_weather", side_effect=lambda city: (_ for _ in ()).throw(
        requests.HTTPError(response=MagicMock(status_code=401))))
    def test_bad_weather_key_returns_401(self, _mock, client):
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert resp.status_code == 401

    @patch("app.fetch_weather", side_effect=requests.RequestException("timeout"))
    def test_network_error_returns_502(self, _mock, client):
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# POST /api/weather — LLM errors
# ---------------------------------------------------------------------------

class TestLLMErrors:
    @patch("app.litellm.completion", side_effect=Exception("rate limit"))
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_llm_error_returns_502(self, _weather, _llm, client):
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert resp.status_code == 502
        assert "LLM error" in resp_json(resp)["error"]

    @patch("app.litellm.completion", side_effect=Exception("auth failed"))
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_llm_error_includes_provider_name(self, _weather, _llm, client):
        resp = client.post("/api/weather", json={"city": "London", "provider": "huggingface"})
        assert "huggingface" in resp_json(resp)["error"]


# ---------------------------------------------------------------------------
# Provider model config integrity
# ---------------------------------------------------------------------------

class TestProviderConfig:
    def test_all_providers_have_default(self):
        for provider, cfg in PROVIDER_MODELS.items():
            assert "default" in cfg, f"{provider} missing 'default'"

    def test_default_is_in_models_list(self):
        for provider, cfg in PROVIDER_MODELS.items():
            ids = [m["id"] for m in cfg["models"]]
            assert cfg["default"] in ids, f"{provider} default not in models list"

    def test_no_duplicate_model_ids(self):
        for provider, cfg in PROVIDER_MODELS.items():
            ids = [m["id"] for m in cfg["models"]]
            assert len(ids) == len(set(ids)), f"{provider} has duplicate model IDs"

    def test_all_models_have_id_and_label(self):
        for provider, cfg in PROVIDER_MODELS.items():
            for m in cfg["models"]:
                assert "id" in m and "label" in m, f"{provider} model missing id/label"


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def resp_json(resp):
    return json.loads(resp.data)
