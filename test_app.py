import json
import time
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
    app_module._weather_cache.clear()
    app_module._llm_cache.clear()
    app_module._rate_limit_store.clear()
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
# Caching — two-tier (weather + LLM)
# ---------------------------------------------------------------------------

MOCK_WEATHER_2 = {
    **MOCK_WEATHER,
    "main": {**MOCK_WEATHER["main"], "temp": 30.0, "feels_like": 28.0},
    "weather": [{"description": "clear sky", "icon": "01d"}],
}

class TestCaching:
    # --- X-Cache header states ---

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_first_request_is_miss(self, _weather, _llm, client):
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert resp.headers.get("X-Cache") == "MISS"

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_second_request_same_provider_is_hit(self, _weather, _llm, client):
        client.post("/api/weather", json={"city": "London", "provider": "groq"})
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert resp.headers.get("X-Cache") == "HIT"

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_different_provider_same_city_is_partial(self, _weather, _llm, client):
        # Weather cached from groq request; LLM cache misses for huggingface key
        client.post("/api/weather", json={"city": "London", "provider": "groq"})
        resp = client.post("/api/weather", json={"city": "London", "provider": "huggingface"})
        assert resp.headers.get("X-Cache") == "PARTIAL"

    # --- Call count guards ---

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_full_hit_makes_zero_external_calls(self, mock_weather, mock_llm, client):
        client.post("/api/weather", json={"city": "London", "provider": "groq"})
        client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert mock_weather.call_count == 1
        assert mock_llm.call_count == 1

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_partial_hit_skips_weather_but_calls_llm(self, mock_weather, mock_llm, client):
        client.post("/api/weather", json={"city": "London", "provider": "groq"})
        client.post("/api/weather", json={"city": "London", "provider": "huggingface"})
        assert mock_weather.call_count == 1
        assert mock_llm.call_count == 2

    # --- Cache key properties ---

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_weather_cache_key_is_case_insensitive(self, mock_weather, _llm, client):
        client.post("/api/weather", json={"city": "London", "provider": "groq"})
        resp = client.post("/api/weather", json={"city": "LONDON", "provider": "groq"})
        assert resp.headers.get("X-Cache") == "HIT"
        assert mock_weather.call_count == 1

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_different_cities_are_independent(self, mock_weather, _llm, client):
        client.post("/api/weather", json={"city": "London", "provider": "groq"})
        resp = client.post("/api/weather", json={"city": "Paris", "provider": "groq"})
        assert resp.headers.get("X-Cache") == "MISS"
        assert mock_weather.call_count == 2

    # --- Hash-based coherence ---

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather")
    def test_weather_change_invalidates_llm_cache(self, mock_weather, mock_llm, client):
        # First request: conditions A → summary cached under hash_A
        mock_weather.return_value = MOCK_WEATHER
        client.post("/api/weather", json={"city": "London", "provider": "groq"})

        # Weather changes → different hash → LLM cache must miss
        mock_weather.return_value = MOCK_WEATHER_2
        app_module._weather_cache.clear()  # force weather re-fetch
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert resp.headers.get("X-Cache") == "MISS"
        assert mock_llm.call_count == 2

    # --- TTL expiry ---

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_expired_weather_entry_triggers_fresh_fetch(self, mock_weather, _llm, client):
        app_module._weather_cache["london"] = (MOCK_WEATHER, time.time() - 1)
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert resp.headers.get("X-Cache") == "MISS"
        assert mock_weather.call_count == 1


# ---------------------------------------------------------------------------
# Rate limiting — sliding window, per IP
# ---------------------------------------------------------------------------

class TestRateLimiting:
    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_requests_within_limit_return_200(self, _w, _l, client):
        for _ in range(10):
            resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
            assert resp.status_code == 200

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_exceeding_limit_returns_429(self, _w, _l, client):
        for _ in range(10):
            client.post("/api/weather", json={"city": "London", "provider": "groq"})
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert resp.status_code == 429

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_429_has_retry_after_header(self, _w, _l, client):
        for _ in range(10):
            client.post("/api/weather", json={"city": "London", "provider": "groq"})
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert "Retry-After" in resp.headers
        assert int(resp.headers["Retry-After"]) > 0

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_429_has_error_message(self, _w, _l, client):
        for _ in range(10):
            client.post("/api/weather", json={"city": "London", "provider": "groq"})
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert "Rate limit" in resp_json(resp)["error"]

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_different_ips_are_independent(self, _w, _l, client):
        for _ in range(10):
            client.post("/api/weather", json={"city": "London", "provider": "groq"},
                        environ_overrides={"REMOTE_ADDR": "1.2.3.4"})
        # Different IP should not be rate limited
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"},
                           environ_overrides={"REMOTE_ADDR": "5.6.7.8"})
        assert resp.status_code == 200

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_expired_timestamps_do_not_count(self, _w, _l, client):
        # Plant 10 timestamps already outside the window
        old = time.time() - app_module.RATE_WINDOW - 1
        app_module._rate_limit_store["127.0.0.1"] = [old] * 10
        resp = client.post("/api/weather", json={"city": "London", "provider": "groq"})
        assert resp.status_code == 200

    @patch("app.litellm.completion", return_value=MOCK_LLM_RESPONSE)
    @patch("app.fetch_weather", return_value=MOCK_WEATHER)
    def test_rate_limit_is_checked_before_body_parsing(self, _w, _l, client):
        # Exhaust limit, then send malformed body — should still get 429, not 400
        for _ in range(10):
            client.post("/api/weather", json={"city": "London", "provider": "groq"})
        resp = client.post("/api/weather", data="not json",
                           content_type="application/json")
        assert resp.status_code == 429


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def resp_json(resp):
    return json.loads(resp.data)
