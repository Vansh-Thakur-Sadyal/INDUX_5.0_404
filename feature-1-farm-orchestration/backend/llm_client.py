# ==========================================
# SHARED MISTRAL API CLIENT
# (replaces the previous local Ollama setup)
# ==========================================

import os
import time
import requests

# Load MISTRAL_API_KEY from a .env file at the project root (or cwd) if present
try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    load_dotenv()  # also pick up a .env in the current working directory
except ImportError:
    pass

MISTRAL_API_URL = "https://api.mistral.ai/v1/chat/completions"
# ── CHANGE THE MODEL HERE ─────────────────────────────
# This one line controls the model for the entire project
# (all 5 agents + chatbot). Any model id from
# https://docs.mistral.ai/getting-started/models/ works.
MISTRAL_MODEL = "ministral-3b-2512"

# Rate limit on this key is ~0.07 requests/second (about 4/minute),
# so 429 responses are expected when the pipeline fires several LLM
# calls back-to-back — we wait and retry instead of failing.
RATE_LIMIT_WAIT_SECONDS = 15


def mistral_chat(messages, temperature=0.3, max_tokens=1024, json_mode=False, retries=3, timeout=60):
    """
    Send a chat request to the Mistral API and return the reply text.

    Args:
        messages: list of {"role": ..., "content": ...} dicts
        json_mode: if True, forces the model to return valid JSON

    Raises:
        RuntimeError if the API key is missing or all attempts fail.
    """
    api_key = os.getenv("MISTRAL_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "MISTRAL_API_KEY is not set. Add it to a .env file at the "
            "project root (MISTRAL_API_KEY=your-key) or set it as an "
            "environment variable."
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MISTRAL_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    last_error = None

    for attempt in range(retries):
        try:
            response = requests.post(
                MISTRAL_API_URL, headers=headers, json=payload, timeout=timeout
            )

            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]

            if response.status_code == 429:
                last_error = "Rate limited (HTTP 429)"
                time.sleep(RATE_LIMIT_WAIT_SECONDS * (attempt + 1))
                continue

            if response.status_code in (401, 403):
                raise RuntimeError(
                    f"Mistral API auth failed (HTTP {response.status_code}). "
                    "Check your MISTRAL_API_KEY."
                )

            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            time.sleep(2)

        except requests.RequestException as e:
            last_error = str(e)
            time.sleep(2)

    raise RuntimeError(f"Mistral API request failed after {retries} attempts: {last_error}")
