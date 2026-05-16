# Weather App — Project Context

## What this is
A Flask web app that fetches real weather data from OpenWeatherMap and uses an LLM (Claude, OpenAI, or Gemini) to generate a friendly natural-language summary. The user picks a provider via UI pills, types a city, and gets live weather + an AI summary.

## Project structure
```
weather-app/
├── app.py                 ← Flask backend: weather fetch + multi-LLM routing
├── requirements.txt       ← flask, requests, litellm, python-dotenv
├── .env.example           ← template for API keys (copy → .env)
├── .env                   ← actual keys (gitignored)
└── templates/
    └── index.html         ← dark glassmorphism UI, provider switcher, result card
```

## How to run
```bash
pip install -r requirements.txt
cp .env.example .env        # then fill in your keys
python app.py               # starts at http://localhost:5000
```

## Required API keys (in .env)
- `OPENWEATHER_API_KEY` — free at openweathermap.org/api (takes ~10 min to activate)
- `ANTHROPIC_API_KEY` — console.anthropic.com (requires billing)
- `OPENAI_API_KEY` — only needed if using OpenAI provider
- `GEMINI_API_KEY` — only needed if using Gemini provider

You only need the keys for the providers you want to use.

## Architecture
1. Frontend POSTs `{ city, provider }` to `/api/weather`
2. Backend calls OpenWeatherMap for real weather data
3. Backend builds a prompt with that real data and calls the selected LLM via `litellm`
4. LLM writes a 2-3 sentence friendly summary
5. Backend returns JSON; frontend renders weather card + AI summary

## LLM provider mapping (app.py)
```python
PROVIDER_MODELS = {
    "claude": "claude-sonnet-4-6",
    "openai": "gpt-4o-mini",
    "gemini": "gemini/gemini-1.5-flash",
}
```
`litellm` handles the unified interface — reads API keys from env automatically.

## Current state
- All files written and complete; app has not been run yet
- No git repo initialized
- Potential next steps:
  - Run `python app.py` and test with a real city
  - Upgrade Claude model to `claude-opus-4-7` for better summaries
  - Add a 5-day forecast section
  - Add a loading spinner
  - Add unit toggle (°C / °F)
  - Deploy to a cloud provider

## Session background
Built in response to: "create a weather app which prompts an LLM to get weather details — compatible with OpenAI, Gemini, and Claude." Uses `litellm` for multi-provider support instead of separate SDKs per provider.
