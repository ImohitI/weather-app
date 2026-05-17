#!/usr/bin/env python3
"""
Discover and live-test free models for each provider, then rewrite models.json.

Usage:
    python update_models.py                     # refresh all providers
    python update_models.py --provider openrouter  # one provider only

Requires the same API keys as the app (.env or environment variables).
HuggingFace has no public model listing API, so a curated candidate list is
tested instead — add new candidates to _HF_CANDIDATES as models are released.
"""

import argparse
import json
import logging
import os
import time

import litellm
import requests
from dotenv import load_dotenv

load_dotenv()

# Silence litellm's red provider-list banners
logging.getLogger("LiteLLM").setLevel(logging.ERROR)
litellm.set_verbose = False

MODELS_PATH  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.json")
TEST_PROMPT  = [{"role": "user", "content": "Reply with one word: ready"}]
TEST_TIMEOUT = 20    # seconds per model call
TEST_TOKENS  = 20
CALL_DELAY   = 1.0   # seconds between calls to avoid hammering providers


# ── shared test helper ───────────────────────────────────────────────────────

def _live_test(model_id: str) -> bool:
    """Return True if the model responds with non-empty content."""
    try:
        resp = litellm.completion(
            model=model_id,
            messages=TEST_PROMPT,
            max_tokens=TEST_TOKENS,
            timeout=TEST_TIMEOUT,
        )
        return bool((resp.choices[0].message.content or "").strip())
    except Exception as exc:
        print(f"    FAIL ({exc.__class__.__name__}): {str(exc)[:80]}")
        return False


# ── OpenRouter ───────────────────────────────────────────────────────────────

# IDs that are not text-generation chat models (audio, routing slots, etc.)
_OR_EXCLUDE = ("lyria", "owl-alpha", "openrouter/free", "cobuddy")

def _or_label(m: dict) -> str:
    return m.get("name") or m["id"].split("/")[-1].replace("-", " ").title()


def discover_openrouter(api_key: str) -> list[dict]:
    print("[OpenRouter] Fetching model list from API...")
    r = requests.get(
        "https://openrouter.ai/api/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    r.raise_for_status()

    free = [
        m for m in r.json()["data"]
        if str(m.get("pricing", {}).get("prompt", "1")) == "0"
        and not any(x in m["id"] for x in _OR_EXCLUDE)
    ]
    print(f"  {len(free)} free models found. Live-testing each...")

    passing = []
    for m in free:
        model_id = f"openrouter/{m['id']}"
        label    = _or_label(m)
        print(f"  {label} ...", end=" ", flush=True)
        time.sleep(CALL_DELAY)
        if _live_test(model_id):
            print("PASS")
            passing.append({"id": model_id, "label": label})
        else:
            print("FAIL")

    print(f"  → {len(passing)} / {len(free)} passed\n")
    return passing


# ── Groq ─────────────────────────────────────────────────────────────────────

# Keywords that identify non-chat models on Groq (speech, safety, deprecated)
_GROQ_EXCLUDE = ("whisper", "guard", "tts", "distil", "vision", "preview")

def _groq_label(model_id: str) -> str:
    # "meta-llama/llama-4-scout-17b-16e-instruct" → "Llama 4 Scout 17B 16E Instruct"
    name = model_id.split("/")[-1].replace("-", " ").title()
    return name


def discover_groq(api_key: str) -> list[dict]:
    print("[Groq] Fetching model list from API...")
    r = requests.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15,
    )
    r.raise_for_status()

    candidates = [
        m for m in r.json()["data"]
        if m.get("active", True)
        and not any(x in m["id"] for x in _GROQ_EXCLUDE)
    ]
    print(f"  {len(candidates)} chat candidates. Live-testing each...")

    passing = []
    for m in candidates:
        model_id = f"groq/{m['id']}"
        label    = _groq_label(m["id"])
        print(f"  {m['id']} ...", end=" ", flush=True)
        time.sleep(CALL_DELAY)
        if _live_test(model_id):
            print("PASS")
            passing.append({"id": model_id, "label": label})
        else:
            print("FAIL")

    print(f"  → {len(passing)} / {len(candidates)} passed\n")
    return passing


# ── HuggingFace ──────────────────────────────────────────────────────────────

# HuggingFace has no public API to list free inference models.
# Add new candidates here as models are released; failing ones are dropped automatically.
_HF_CANDIDATES = [
    ("huggingface/Qwen/Qwen2.5-7B-Instruct",           "Qwen 2.5 7B"),
    ("huggingface/Qwen/Qwen2.5-72B-Instruct",           "Qwen 2.5 72B"),
    ("huggingface/google/gemma-2-2b-it",                "Gemma 2 2B"),
    ("huggingface/google/gemma-2-9b-it",                "Gemma 2 9B"),
    ("huggingface/meta-llama/Llama-3.2-1B-Instruct",   "Llama 3.2 1B"),
    ("huggingface/meta-llama/Llama-3.2-3B-Instruct",   "Llama 3.2 3B"),
    ("huggingface/meta-llama/Llama-3.1-8B-Instruct",   "Llama 3.1 8B"),
    ("huggingface/mistralai/Mistral-7B-Instruct-v0.3",  "Mistral 7B"),
    ("huggingface/microsoft/Phi-3.5-mini-instruct",     "Phi 3.5 Mini"),
]


def discover_huggingface() -> list[dict]:
    print("[HuggingFace] No listing API — testing curated candidates...")

    passing = []
    for model_id, label in _HF_CANDIDATES:
        print(f"  {label} ...", end=" ", flush=True)
        time.sleep(CALL_DELAY)
        if _live_test(model_id):
            print("PASS")
            passing.append({"id": model_id, "label": label})
        else:
            print("FAIL")

    print(f"  → {len(passing)} / {len(_HF_CANDIDATES)} passed\n")
    return passing


# ── write helpers ─────────────────────────────────────────────────────────────

def _pick_default(models: list[dict], current_default: str) -> str:
    """Keep the existing default if it still passes, otherwise use the first passer."""
    ids = [m["id"] for m in models]
    return current_default if current_default in ids else ids[0]


def _update(config: dict, provider: str, new_models: list[dict]) -> None:
    if not new_models:
        print(f"  No passing models for {provider} — keeping existing list unchanged.")
        return
    config[provider]["default"] = _pick_default(new_models, config[provider]["default"])
    config[provider]["models"]  = new_models


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh models.json with live-tested models")
    parser.add_argument(
        "--provider", choices=["groq", "openrouter", "huggingface"],
        help="Refresh one provider only (default: all)",
    )
    args = parser.parse_args()

    with open(MODELS_PATH) as f:
        config = json.load(f)

    groq_key = os.getenv("GROQ_API_KEY")
    or_key   = os.getenv("OPENROUTER_API_KEY")
    hf_key   = os.getenv("HUGGINGFACE_API_KEY")

    run_all = args.provider is None

    if run_all or args.provider == "openrouter":
        if not or_key:
            print("[OpenRouter] OPENROUTER_API_KEY not set — skipping\n")
        else:
            _update(config, "openrouter", discover_openrouter(or_key))

    if run_all or args.provider == "groq":
        if not groq_key:
            print("[Groq] GROQ_API_KEY not set — skipping\n")
        else:
            _update(config, "groq", discover_groq(groq_key))

    if run_all or args.provider == "huggingface":
        if not hf_key:
            print("[HuggingFace] HUGGINGFACE_API_KEY not set — skipping\n")
        else:
            _update(config, "huggingface", discover_huggingface())

    with open(MODELS_PATH, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")

    print("models.json updated:")
    for provider, cfg in config.items():
        names = ", ".join(m["label"] for m in cfg["models"])
        print(f"  {provider}: [{names}]  default={cfg['default'].split('/')[-1]}")


if __name__ == "__main__":
    main()
