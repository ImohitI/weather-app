# Weather App — Project Context

## What this is
A Flask web app that fetches real weather data from OpenWeatherMap and uses an LLM to generate a friendly natural-language summary. The user picks a provider and model via UI, types a city, and gets live weather + an AI summary.

**Live URL:** https://weather-app-r27m.onrender.com/
**GitHub:** https://github.com/ImohitI/weather-app

## Project structure
```
weather-app/
├── app.py                 ← Flask backend: weather fetch + multi-LLM routing
├── requirements.txt       ← flask, requests, litellm, python-dotenv, gunicorn
├── test_app.py            ← 34 unit tests (mocked, no API calls needed)
├── .env.example           ← template for API keys (copy → .env)
├── .env                   ← actual keys (gitignored, never commit)
├── .gitignore             ← excludes .env, venv/, __pycache__, .claude/
├── .github/
│   └── workflows/
│       └── tests.yml      ← GitHub Actions: runs 34 unit tests on every push to main
└── templates/
    └── index.html         ← dark glassmorphism UI, provider + model switcher
```

## How to run locally
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in your keys
python app.py               # starts at http://localhost:5000
```

## How to run tests
```bash
source venv/bin/activate
python -m pytest test_app.py -v
```

## Required API keys (in .env)
- `OPENWEATHER_API_KEY` — free at openweathermap.org/api
- `GROQ_API_KEY` — free at console.groq.com (recommended, no credit card)
- `HUGGINGFACE_API_KEY` — free at huggingface.co/settings/tokens
- `OPENROUTER_API_KEY` — free models at openrouter.ai

## Architecture
1. Frontend POSTs `{ city, provider, model }` to `/api/weather`
2. Backend calls OpenWeatherMap for real weather data
3. Backend builds a prompt and calls the selected LLM via `litellm`
4. LLM writes a 2-3 sentence friendly summary
5. Backend returns JSON; frontend renders weather card + AI summary
6. `/api/models` endpoint returns available models per provider for the dropdown

## LLM providers and models (app.py)
```python
PROVIDER_MODELS = {
    "groq": {
        "default": "groq/llama-3.3-70b-versatile",
        "models": [Llama 3.3 70B, Llama 3.1 8B, Llama 4 Scout, Qwen3 32B]
    },
    "openrouter": {
        "default": "openrouter/meta-llama/llama-3.1-8b-instruct:free",
        "models": [Llama 3.1 8B, Mistral 7B, Gemma 3 1B, Phi-3 Mini]
    },
    "huggingface": {
        "default": "huggingface/Qwen/Qwen2.5-7B-Instruct",
        "models": [Qwen 2.5 7B, Gemma 2 2B, Llama 3.2 1B]
    },
}
```
`litellm` handles the unified interface — reads API keys from env automatically.

## CI — GitHub Actions
- Workflow at `.github/workflows/tests.yml`
- Triggers on every push and pull request to `main`
- Runs `python -m pytest test_app.py -v` (34 tests)
- Sets `OPENWEATHER_API_KEY=dummy_key_for_tests` as env var — required because the app checks for the key before reaching mocked code; the actual value is never used in tests
- Results visible at: github.com/ImohitI/weather-app → Actions tab

## Caching strategy (two-tier, hash-based coherence)

Two in-process dicts act as independent cache tiers. In production, both would be Redis.

### Tier 1 — Weather data
- **Key:** `city.lower()`
- **TTL:** 10 minutes
- **Why:** OpenWeatherMap is rate-limited; weather changes slowly

### Tier 2 — LLM summary
- **Key:** `city:provider:model:<weather_hash>`
- **TTL:** 1 hour (generous, hash handles staleness)
- **Why:** LLM calls are 10–20× slower and token-expensive

### Weather hash
`_weather_hash(weather)` MD5s only the five fields that appear in the prompt
(`temp`, `feels_like`, `description`, `humidity`, `wind_speed`).
This ties the summary to the exact conditions it was generated for — if weather
changes, the hash changes and the LLM cache misses automatically. Stale summaries
describing yesterday's rain during today's sunshine are impossible.

### X-Cache response header
| Value | Meaning |
|---|---|
| `MISS` | Both caches cold — weather API + LLM called |
| `PARTIAL` | Weather cached, LLM miss — only LLM called (different provider/model) |
| `HIT` | Both caches warm — zero external calls |

### Known limitations (interview talking points)
- **Cache stampede:** two concurrent requests for the same cold city both call the API. Fix: a per-key lock or "promise" pattern.
- **Memory growth:** expired entries are only evicted on read. Fix: Redis with native TTL eviction.
- **Single process:** in-process dicts are not shared across gunicorn workers. Fix: Redis (makes the app stateless and horizontally scalable — see TODO item #6).

## Key decisions made during development
- **Dropped Claude/OpenAI/Gemini** — replaced with free providers (Groq, HuggingFace, OpenRouter)
- **Gemini free tier unavailable** in some regions (India) — limit shows as 0
- **Groq model changes** — Mixtral 8x7B, Gemma2 9B, DeepSeek R1, QwQ 32B all decommissioned; replaced with active models verified via Groq API
- **HuggingFace model selection** — many models not chat-compatible; Qwen2.5-7B, Gemma-2-2B, Llama-3.2-1B confirmed working
- **gunicorn added** to requirements.txt for production deployment (Flask dev server not suitable)

## Deployment (Render)
- **Platform:** render.com (free tier)
- **Build command:** `pip install -r requirements.txt`
- **Start command:** `gunicorn app:app`
- **Environment variables:** set in Render dashboard (never in code)
- **Auto-deploy:** every `git push` to main triggers a redeploy
- **Cold start:** ~30 seconds after 15 min inactivity on free tier

## Deploying updates
```bash
git add <changed files>
git commit -m "your message"
git push
```
Render picks up the push and redeploys automatically.

## Testing report (all models verified)
| Provider | Model | Status |
|---|---|---|
| Groq | Llama 3.3 70B | ✅ |
| Groq | Llama 3.1 8B | ✅ |
| Groq | Llama 4 Scout | ✅ |
| Groq | Qwen3 32B | ✅ |
| HuggingFace | Qwen 2.5 7B | ✅ |
| HuggingFace | Gemma 2 2B | ✅ |
| HuggingFace | Llama 3.2 1B | ✅ |
| OpenRouter | All 4 | ⚠️ Needs OPENROUTER_API_KEY in Render env vars |

## Potential next steps
- Add OPENROUTER_API_KEY to Render environment variables and verify
- Add a loading spinner
- Add °C / °F toggle
- Add 5-day forecast section
- Add error boundary for cold start delay on Render
