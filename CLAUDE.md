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

## Client-server split — why logic lives in two files

Two physically separate machines are involved on every request:

```
Browser (client)                 Server (Render / localhost)
────────────────                 ───────────────────────────
index.html <script>              app.py
JavaScript                       Python
Runs on user's machine           Runs on the server
Owns: DOM, events, rendering     Owns: API keys, shared cache, external calls
```

**Python owns what only the server should touch:**
- API keys — if these ran in JS they'd be visible in browser DevTools to anyone
- `_weather_cache` / `_llm_cache` — shared across all users; must live in one place
- External API calls (OWM, Groq, HuggingFace) — called server-to-server, not from browser
- Input validation that users shouldn't be able to bypass

**JavaScript owns what only the browser can do:**
- DOM updates — showing/hiding the result card, writing weather values
- User events — button clicks, Enter key, provider pill switching
- `updateModelDropdown()` — rebuilds `<select>` on provider switch with zero network calls
- Button disable during request — prevents double-submit
- `<img src>` assignment — browser fetches the OWM icon from CDN natively

**The contract between them — two endpoints only:**
```
GET  /api/models   →  called once on page load, stored in JS providerModels
POST /api/weather  →  called on each search, response rendered into DOM
```

Neither side knows how the other is implemented. Rule: **secrets + shared state → server. Events + rendering → browser.**

## Full request lifecycle

### Phase 1 — Page load
| Step | What happens |
|---|---|
| `GET /` | Flask `index()` returns rendered `index.html` via Jinja2 |
| Script executes | JS initializes `activeProvider = "groq"`, attaches event listeners |
| `GET /api/models` | JS fires immediately; backend reshapes `PROVIDER_MODELS` dict → JSON |
| Dropdown populated | JS stores response in `providerModels`, builds `<select>` options |

`/api/models` is called once on page load and cached in JS memory. Provider switching never hits the network — it just re-renders the dropdown from `providerModels`.

### Phase 2 — Search request
| Step | What happens |
|---|---|
| `search()` | JS disables button, hides previous result, calls `fetch("/api/weather", POST)` |
| WSGI | Gunicorn worker (or Werkzeug dev server) reads TCP bytes, calls Flask WSGI callable |
| Routing | Flask O(1) URL map lookup → `get_weather()` |
| Body parse | `request.get_json(silent=True)` → `json.loads()` on raw bytes |
| Validation | 4 guard clauses (empty city, unknown provider, missing API key) — all short-circuit |
| Tier 1 cache | `_cache_get(_weather_cache, city.lower())` — O(1) dict lookup + float TTL comparison |
| Weather fetch (miss) | `requests.get()` → DNS → TCP pool → TLS → HTTP → `resp.raise_for_status()` → `resp.json()` |
| Hash | `_weather_hash()` MD5s 5 prompt-relevant fields, takes first 8 hex chars |
| Tier 2 cache | `_cache_get(_llm_cache, "city:provider:model:hash")` |
| LLM call (miss) | `litellm.completion()` → reads env key → HTTPS POST to provider API → 1-5s |
| Response | `jsonify()` + `X-Cache` header (HIT / PARTIAL / MISS) |
| DOM update | JS writes 10 elements; browser fetches icon PNG from OWM CDN separately |

### WSGI and worker model
- **Dev** (`python app.py`): single Werkzeug process — one request at a time
- **Prod** (`gunicorn app:app`): pre-forked worker processes — each has its own `_weather_cache` / `_llm_cache` in memory, not shared. This is why Redis is needed for horizontal scaling (TODO #6)

### What `litellm` does
Unified wrapper over multiple provider APIs. Parses the model prefix (`groq/`, `huggingface/`, `openrouter/`), reads the matching `*_API_KEY` from env, translates the OpenAI-style `messages` format into the provider's actual request format, and returns a response object with the same shape regardless of provider.

### Why `icon_code` is a code not a URL
Backend returns `"10d"` — the browser constructs `https://openweathermap.org/img/wn/10d@2x.png` and fetches it directly from OWM's CDN. Backend never touches the image.

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
- **TTL:** 1 hour — purely a memory eviction policy, not a freshness policy
- **Why:** LLM calls are 10–20× slower and token-expensive

### TTL purpose differs per tier
The two TTLs serve completely different concerns:
- **Weather TTL** = freshness policy — how long is this fact still true? (OpenWeatherMap updates ~every 10 min)
- **LLM TTL** = eviction policy — how long do we keep this entry in memory if nobody asks for it again?

The LLM cache needs no freshness TTL because the hash handles that. If weather changes → hash changes → old LLM entry is unreachable, never served again. It just leaks memory without a TTL. You could set LLM TTL to 24h or a week with zero correctness impact.

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
