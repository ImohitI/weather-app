import os
import requests
import litellm
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")

PROVIDER_MODELS = {
    "groq": {
        "default": "groq/llama-3.3-70b-versatile",
        "models": [
            {"id": "groq/llama-3.3-70b-versatile",          "label": "Llama 3.3 70B"},
            {"id": "groq/llama-3.1-8b-instant",              "label": "Llama 3.1 8B"},
            {"id": "groq/meta-llama/llama-4-scout-17b-16e-instruct", "label": "Llama 4 Scout"},
            {"id": "groq/qwen/qwen3-32b",                         "label": "Qwen3 32B"},
        ],
    },
    "openrouter": {
        "default": "openrouter/meta-llama/llama-3.1-8b-instruct:free",
        "models": [
            {"id": "openrouter/meta-llama/llama-3.1-8b-instruct:free",        "label": "Llama 3.1 8B"},
            {"id": "openrouter/mistralai/mistral-7b-instruct:free",            "label": "Mistral 7B"},
            {"id": "openrouter/google/gemma-3-1b-it:free",                     "label": "Gemma 3 1B"},
            {"id": "openrouter/microsoft/phi-3-mini-128k-instruct:free",       "label": "Phi-3 Mini"},
        ],
    },
    "huggingface": {
        "default": "huggingface/Qwen/Qwen2.5-7B-Instruct",
        "models": [
            {"id": "huggingface/Qwen/Qwen2.5-7B-Instruct",         "label": "Qwen 2.5 7B"},
            {"id": "huggingface/google/gemma-2-2b-it",               "label": "Gemma 2 2B"},
            {"id": "huggingface/meta-llama/Llama-3.2-1B-Instruct",  "label": "Llama 3.2 1B"},
        ],
    },
}


def fetch_weather(city: str) -> dict:
    url = "https://api.openweathermap.org/data/2.5/weather"
    resp = requests.get(
        url,
        params={"q": city, "appid": OPENWEATHER_API_KEY, "units": "metric"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def build_prompt(data: dict, city: str) -> str:
    return (
        f"Current weather in {city}:\n"
        f"- Temperature: {data['main']['temp']}°C "
        f"(feels like {data['main']['feels_like']}°C)\n"
        f"- Condition: {data['weather'][0]['description']}\n"
        f"- Humidity: {data['main']['humidity']}%\n"
        f"- Wind: {data['wind']['speed']} m/s\n\n"
        "Give a friendly 2-3 sentence weather summary for someone planning their day. "
        "Include practical clothing or activity tips."
    )


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/models")
def get_models():
    return jsonify({
        provider: [{"id": m["id"], "label": m["label"]} for m in cfg["models"]]
        for provider, cfg in PROVIDER_MODELS.items()
    })


@app.route("/api/weather", methods=["POST"])
def get_weather():
    body = request.get_json(silent=True) or {}
    city = body.get("city", "").strip()
    provider = body.get("provider", "groq")
    model_id = body.get("model", "")

    if not city:
        return jsonify({"error": "City name is required"}), 400

    if provider not in PROVIDER_MODELS:
        return jsonify({"error": f"Unknown provider '{provider}'"}), 400

    valid_ids = [m["id"] for m in PROVIDER_MODELS[provider]["models"]]
    if not model_id or model_id not in valid_ids:
        model_id = PROVIDER_MODELS[provider]["default"]

    if not OPENWEATHER_API_KEY:
        return jsonify({"error": "OPENWEATHER_API_KEY is not set"}), 500

    # 1. Fetch real weather data
    try:
        weather = fetch_weather(city)
    except requests.HTTPError as exc:
        code = exc.response.status_code
        if code == 404:
            return jsonify({"error": f"City '{city}' not found"}), 404
        if code == 401:
            return jsonify({"error": "Invalid OpenWeatherMap API key"}), 401
        return jsonify({"error": "Weather service error"}), 502
    except requests.RequestException:
        return jsonify({"error": "Could not reach weather service"}), 502

    # 2. Ask the selected LLM to summarise it
    try:
        llm_resp = litellm.completion(
            model=model_id,
            messages=[{"role": "user", "content": build_prompt(weather, city)}],
            max_tokens=200,
        )
        summary = llm_resp.choices[0].message.content.strip()
    except Exception as exc:  # litellm raises various provider-specific errors
        return jsonify({"error": f"LLM error ({provider}): {exc}"}), 502

    return jsonify({
        "city": weather["name"],
        "country": weather["sys"]["country"],
        "temp": round(weather["main"]["temp"], 1),
        "feels_like": round(weather["main"]["feels_like"], 1),
        "humidity": weather["main"]["humidity"],
        "wind_speed": weather["wind"]["speed"],
        "description": weather["weather"][0]["description"].title(),
        "icon_code": weather["weather"][0]["icon"],
        "summary": summary,
        "provider": provider,
        "model_id": model_id,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
