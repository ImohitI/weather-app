import os
import time
import json
import sqlite3
import hashlib
import requests
import litellm
from flask import Flask, jsonify, render_template, request
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Database — SQLite, path is overridden in tests to a temp file
DATABASE = "history.db"


def _init_db():
    conn = sqlite3.connect(DATABASE)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS query_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                city        TEXT    NOT NULL,
                country     TEXT    NOT NULL,
                provider    TEXT    NOT NULL,
                model_id    TEXT    NOT NULL,
                temp        REAL    NOT NULL,
                description TEXT    NOT NULL,
                summary     TEXT    NOT NULL,
                timestamp   TEXT    NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_city      ON query_history(city);
            CREATE INDEX IF NOT EXISTS idx_timestamp ON query_history(timestamp);
        """)
        conn.commit()
    finally:
        conn.close()


def _save_history(city, country, provider, model_id, temp, description, summary):
    conn = sqlite3.connect(DATABASE)
    try:
        conn.execute(
            "INSERT INTO query_history "
            "(city, country, provider, model_id, temp, description, summary) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (city, country, provider, model_id, temp, description, summary),
        )
        conn.commit()
    finally:
        conn.close()


_init_db()

# Two-tier cache
# Tier 1 — raw weather data: city.lower() -> (weather_dict, expires_at)
_weather_cache: dict = {}
CACHE_TTL = 600       # 10 minutes

# Tier 2 — LLM summaries: "city:provider:model:weather_hash" -> (summary, expires_at)
# Hash-based key means a weather change automatically invalidates the summary.
_llm_cache: dict = {}
LLM_CACHE_TTL = 3600  # 1 hour (hash handles coherence; TTL handles memory)


def _cache_get(cache: dict, key: str):
    entry = cache.get(key)
    if not entry:
        return None
    value, expires_at = entry
    if time.time() > expires_at:
        del cache[key]
        return None
    return value


def _cache_set(cache: dict, key: str, value, ttl: int):
    cache[key] = (value, time.time() + ttl)


# Sliding-window rate limiter: ip -> [timestamp, ...]
_rate_limit_store: dict = {}
RATE_LIMIT  = 10   # max requests per window per IP
RATE_WINDOW = 60   # window size in seconds


def _is_rate_limited(ip: str) -> tuple[bool, int]:
    now          = time.time()
    window_start = now - RATE_WINDOW

    # Keep only timestamps inside the current window (lazy cleanup)
    timestamps = [t for t in _rate_limit_store.get(ip, []) if t > window_start]

    if len(timestamps) >= RATE_LIMIT:
        # Seconds until the oldest request rolls out of the window
        retry_after = int(timestamps[0] + RATE_WINDOW - now) + 1
        _rate_limit_store[ip] = timestamps
        return True, retry_after

    timestamps.append(now)
    _rate_limit_store[ip] = timestamps
    return False, 0


def _weather_hash(weather: dict) -> str:
    """Short hash of the fields that appear in the LLM prompt."""
    relevant = {
        "temp":        weather["main"]["temp"],
        "feels_like":  weather["main"]["feels_like"],
        "description": weather["weather"][0]["description"],
        "humidity":    weather["main"]["humidity"],
        "wind_speed":  weather["wind"]["speed"],
    }
    return hashlib.md5(json.dumps(relevant, sort_keys=True).encode()).hexdigest()[:8]

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
GROQ_API_KEY        = os.getenv("GROQ_API_KEY")
OPENROUTER_API_KEY  = os.getenv("OPENROUTER_API_KEY")
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY")

# Maps provider name → the env var name expected, used in error messages
PROVIDER_KEY_NAMES = {
    "groq":        "GROQ_API_KEY",
    "openrouter":  "OPENROUTER_API_KEY",
    "huggingface": "HUGGINGFACE_API_KEY",
}

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


@app.route("/api/history")
def get_history():
    page       = request.args.get("page", 1, type=int)
    per_page   = min(request.args.get("per_page", 10, type=int), 50)  # cap at 50
    city_query = request.args.get("city", "").strip()
    offset     = (page - 1) * per_page

    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        if city_query:
            pattern = f"%{city_query}%"
            total = conn.execute(
                "SELECT COUNT(*) FROM query_history WHERE city LIKE ?", (pattern,)
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT city, country, provider, model_id, temp, description, summary, timestamp "
                "FROM query_history WHERE city LIKE ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                (pattern, per_page, offset),
            ).fetchall()
        else:
            total = conn.execute("SELECT COUNT(*) FROM query_history").fetchone()[0]
            rows = conn.execute(
                "SELECT city, country, provider, model_id, temp, description, summary, timestamp "
                "FROM query_history ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                (per_page, offset),
            ).fetchall()
    finally:
        conn.close()

    return jsonify({
        "items":    [dict(r) for r in rows],
        "total":    total,
        "page":     page,
        "pages":    (total + per_page - 1) // per_page,
        "per_page": per_page,
    })


@app.route("/api/models")
def get_models():
    return jsonify({
        provider: [{"id": m["id"], "label": m["label"]} for m in cfg["models"]]
        for provider, cfg in PROVIDER_MODELS.items()
    })


@app.route("/api/weather", methods=["POST"])
def get_weather():
    # Rate limit before any parsing — blocked requests do zero work
    client_ip = request.headers.get("X-Forwarded-For", request.remote_addr)
    client_ip = client_ip.split(",")[0].strip()  # X-Forwarded-For can be comma-separated
    limited, retry_after = _is_rate_limited(client_ip)
    if limited:
        resp = jsonify({"error": "Rate limit exceeded. Try again later."})
        resp.headers["Retry-After"] = str(retry_after)
        return resp, 429

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

    provider_api_key = {
        "groq":        GROQ_API_KEY,
        "openrouter":  OPENROUTER_API_KEY,
        "huggingface": HUGGINGFACE_API_KEY,
    }[provider]
    if not provider_api_key:
        return jsonify({"error": f"{PROVIDER_KEY_NAMES[provider]} is not set"}), 500

    # Tier 1 — weather data (keyed by city)
    weather = _cache_get(_weather_cache, city.lower())
    weather_hit = weather is not None

    if not weather_hit:
        try:
            weather = fetch_weather(city)
            _cache_set(_weather_cache, city.lower(), weather, CACHE_TTL)
        except requests.HTTPError as exc:
            code = exc.response.status_code
            if code == 404:
                return jsonify({"error": f"City '{city}' not found"}), 404
            if code == 401:
                return jsonify({"error": "Invalid OpenWeatherMap API key"}), 401
            return jsonify({"error": "Weather service error"}), 502
        except requests.RequestException:
            return jsonify({"error": "Could not reach weather service"}), 502

    # Tier 2 — LLM summary (keyed by city + provider + model + weather hash)
    # Weather hash ties the summary to the exact conditions it was generated for.
    # If weather changes, the hash changes and this key misses automatically.
    llm_key = f"{city.lower()}:{provider}:{model_id}:{_weather_hash(weather)}"
    summary = _cache_get(_llm_cache, llm_key)
    llm_hit = summary is not None

    if not llm_hit:
        try:
            llm_resp = litellm.completion(
                model=model_id,
                messages=[{"role": "user", "content": build_prompt(weather, city)}],
                max_tokens=200,
            )
            summary = llm_resp.choices[0].message.content.strip()
            _cache_set(_llm_cache, llm_key, summary, LLM_CACHE_TTL)
        except Exception as exc:  # litellm raises various provider-specific errors
            return jsonify({"error": f"LLM error ({provider}): {exc}"}), 502

    # HIT = zero external calls | PARTIAL = only LLM called | MISS = both called
    if weather_hit and llm_hit:
        cache_status = "HIT"
    elif weather_hit:
        cache_status = "PARTIAL"
    else:
        cache_status = "MISS"

    resp = jsonify({
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
    resp.headers["X-Cache"] = cache_status

    # Persist to history — non-critical, never fail a user request over a DB error
    try:
        _save_history(
            city=weather["name"],
            country=weather["sys"]["country"],
            provider=provider,
            model_id=model_id,
            temp=round(weather["main"]["temp"], 1),
            description=weather["weather"][0]["description"].title(),
            summary=summary,
        )
    except Exception:
        pass

    return resp


if __name__ == "__main__":
    app.run(debug=True, port=5000)
