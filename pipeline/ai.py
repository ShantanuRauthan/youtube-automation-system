"""Free AI provider abstraction.

Supports:
  - "gemini": Google Gemini free tier (needs GEMINI_API_KEY, no cost).
  - "groq":   free hosted open-source Llama models (needs GROQ_API_KEY, no cost).
  - "ollama": fully local + offline models via the Ollama server (no cost).

Both expose a single `generate_json` helper that returns parsed JSON, and a
`generate_text` helper for free-form text.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from config import config


class AIError(RuntimeError):
    pass


def _extract_json(raw: str) -> Any:
    """Pull the first JSON object/array out of a model response."""
    raw = raw.strip()
    # Strip markdown code fences if present.
    fenced = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    # Find the outermost JSON structure.
    for opener, closer in (("[", "]"), ("{", "}")):
        start = raw.find(opener)
        end = raw.rfind(closer)
        if start != -1 and end != -1 and end > start:
            candidate = raw[start : end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    raise AIError(f"Could not parse JSON from AI response:\n{raw[:500]}")


# --------------------------------------------------------------------------- #
#  Gemini
# --------------------------------------------------------------------------- #
# Tried in order when the configured model is unavailable/retired. These are
# stable alias names that Google keeps pointing at the current flash/pro models,
# so they survive individual model retirements (e.g. gemini-2.0-flash going 404).
_GEMINI_FALLBACKS = [
    "gemini-flash-latest",
    "gemini-2.5-flash",
    "gemini-flash-lite-latest",
    "gemini-2.5-pro",
]

# Remembers a model name that worked so we don't re-probe on every call.
_gemini_working_model: str | None = None


def _is_model_missing(msg: str) -> bool:
    return "not found" in msg or "not available" in msg or "unsupported" in msg or "404" in msg


def _is_rate_limited(msg: str) -> bool:
    return "429" in msg or "quota" in msg or "resource_exhausted" in msg or "rate limit" in msg


def _is_daily_quota(msg: str) -> bool:
    """A per-DAY free-tier cap (e.g. 20 requests/day). Retrying is pointless —
    it only resets after ~24h — so we fail fast instead of sleeping."""
    m = msg.lower().replace(" ", "").replace("_", "")
    return "perday" in m or "requestsperday" in m


def _parse_retry_delay(msg: str, default: float = 30.0) -> float:
    """Pull the server-suggested wait (in seconds) out of a 429 error, +1s buffer."""
    m = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", msg)
    if m:
        return float(m.group(1)) + 1
    m = re.search(r"retry in ([\d.]+)s", msg)
    if m:
        return float(m.group(1)) + 1
    return default


def _gemini_generate(prompt: str) -> str:
    global _gemini_working_model
    try:
        import google.generativeai as genai
    except ImportError as exc:  # pragma: no cover
        raise AIError("google-generativeai is not installed. Run: pip install google-generativeai") from exc

    if not config.gemini_api_key:
        raise AIError("GEMINI_API_KEY is not set.")

    genai.configure(api_key=config.gemini_api_key)

    # Candidate order: a previously-working model, then the configured one, then fallbacks.
    candidates: list[str] = []
    for name in [_gemini_working_model, config.gemini_model, *_GEMINI_FALLBACKS]:
        if name and name not in candidates:
            candidates.append(name)

    max_retries = int(getattr(config, "gemini_max_retries", 5))

    last_error: Exception | None = None
    for name in candidates:
        model = genai.GenerativeModel(name)
        # Retry loop for this model, to ride out free-tier rate limits
        # (the free tier allows only 5 requests/minute).
        attempts = 0
        while True:
            try:
                resp = model.generate_content(prompt)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if _is_model_missing(msg):
                    # This model is gone/unavailable — try the next candidate.
                    last_error = exc
                    break
                if _is_rate_limited(msg):
                    # A per-DAY cap won't clear by waiting — stop immediately.
                    if _is_daily_quota(str(exc)):
                        raise AIError(
                            "Gemini free-tier DAILY quota exhausted (about 20 requests/day). "
                            "This resets in ~24h and cannot be fixed by retrying. Your best options:\n"
                            "  1. Switch to Groq (free, much higher limits): set AI_PROVIDER=groq in .env "
                            "and add GROQ_API_KEY (https://console.groq.com/keys).\n"
                            "  2. Switch to a fully-offline local model: set AI_PROVIDER=ollama in .env "
                            "(install from https://ollama.com, run `ollama pull llama3.1`, then `ollama serve`).\n"
                            "  3. Wait for the daily quota to reset, then re-run."
                        ) from exc
                    attempts += 1
                    if attempts > max_retries:
                        raise AIError(
                            "Gemini free-tier per-minute quota exhausted (5 requests/minute). "
                            "Wait a minute and re-run, set AI_PROVIDER=groq or AI_PROVIDER=ollama in .env, "
                            "or raise GEMINI_MAX_RETRIES. "
                            f"Original error: {exc}"
                        ) from exc
                    delay = _parse_retry_delay(str(exc))
                    print(
                        f"  -> Gemini rate limit hit; waiting {delay:.0f}s then retrying "
                        f"(attempt {attempts}/{max_retries})"
                    )
                    time.sleep(delay)
                    continue
                raise AIError(f"Gemini request failed: {exc}") from exc

            if not getattr(resp, "text", None):
                last_error = AIError("Gemini returned an empty response.")
                break  # try the next candidate model

            if name != _gemini_working_model:
                _gemini_working_model = name
                if name != config.gemini_model:
                    print(f"  -> Gemini model '{config.gemini_model}' unavailable; using '{name}' instead.")
            return resp.text

    raise AIError(
        "No usable Gemini model found. Set GEMINI_MODEL in .env to a current model "
        f"(e.g. 'gemini-flash-latest'). Last error: {last_error}"
    )


# --------------------------------------------------------------------------- #
#  Groq (free, hosted open-source models — OpenAI-compatible API)
# --------------------------------------------------------------------------- #
# Preference order used ONLY to pick among models Groq currently lists as live.
# Groq decommissions models often (e.g. llama3-70b-8192), so we never trust a
# hardcoded name — we fetch the live catalog and choose from it. These are just
# "nice to have first if present"; unknown/retired names are silently ignored.
_GROQ_PREFERRED = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "moonshotai/kimi-k2-instruct",
    "gemma2-9b-it",
]

# Substrings marking models that are NOT general chat models (skip them when
# auto-picking): speech-to-text, text-to-speech, guardrails, embeddings, etc.
_GROQ_NON_CHAT = ("whisper", "tts", "guard", "embed", "prompt-guard", "distil-whisper")

_groq_working_model: str | None = None
_groq_live_models: list[str] | None = None


def _groq_list_models() -> list[str]:
    """Fetch the models Groq currently serves for this key (cached).

    Uses the OpenAI-compatible GET /models endpoint. Returns [] on any failure
    so callers can fall back to the configured/preferred names.
    """
    global _groq_live_models
    if _groq_live_models is not None:
        return _groq_live_models
    try:
        resp = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {config.groq_api_key}"},
            timeout=30,
        )
        if resp.status_code != 200:
            _groq_live_models = []
            return _groq_live_models
        ids = [m.get("id", "") for m in resp.json().get("data", []) if m.get("id")]
        _groq_live_models = ids
    except requests.RequestException:
        _groq_live_models = []
    return _groq_live_models


def _groq_chat_candidates() -> tuple[list[str], list[str]]:
    """Build the ordered list of chat models to try, and the live catalog.

    Order: remembered working model, the configured GROQ_MODEL, then preferred
    names — but every candidate is filtered to what Groq actually lists as live
    (when the catalog is available), so decommissioned names never get tried.
    """
    live = _groq_list_models()
    live_set = set(live)

    def is_chat(name: str) -> bool:
        low = name.lower()
        return not any(tok in low for tok in _GROQ_NON_CHAT)

    ordered: list[str] = []
    for name in [_groq_working_model, config.groq_model, *_GROQ_PREFERRED]:
        if name and name not in ordered and (not live_set or name in live_set):
            ordered.append(name)

    # Append any remaining live chat models we didn't already list, so we always
    # have something valid to try even if the preferred names are all gone.
    for name in live:
        if name not in ordered and is_chat(name):
            ordered.append(name)

    return ordered, live


def _groq_generate(prompt: str) -> str:
    global _groq_working_model
    if not config.groq_api_key:
        raise AIError("GROQ_API_KEY is not set. Get a free key at https://console.groq.com/keys")

    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {config.groq_api_key}",
        "Content-Type": "application/json",
    }

    candidates, live = _groq_chat_candidates()
    if not candidates:
        hint = f" Live models for your key: {', '.join(live)}" if live else ""
        raise AIError(
            "Groq returned no usable chat models for your key. Set GROQ_MODEL in .env "
            "to a current model from https://console.groq.com/docs/models." + hint
        )

    max_retries = int(getattr(config, "gemini_max_retries", 5))
    last_error: Exception | None = None

    for name in candidates:
        attempts = 0
        while True:
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json={
                        "model": name,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.7,
                    },
                    timeout=120,
                )
            except requests.RequestException as exc:
                raise AIError(f"Could not reach Groq: {exc}") from exc

            if resp.status_code == 429:
                # Rate limited — Groq's free tier is generous but has per-minute caps.
                attempts += 1
                if attempts > max_retries:
                    raise AIError(
                        "Groq rate limit hit repeatedly. Wait a moment and re-run, or set "
                        "AI_PROVIDER=ollama in .env for unlimited offline use."
                    )
                retry_after = resp.headers.get("retry-after")
                delay = float(retry_after) + 1 if retry_after else 15.0
                print(
                    f"  -> Groq rate limit hit; waiting {delay:.0f}s then retrying "
                    f"(attempt {attempts}/{max_retries})"
                )
                time.sleep(delay)
                continue

            if resp.status_code in (400, 404) and "model" in resp.text.lower():
                # Model decommissioned/unknown — try the next candidate.
                last_error = AIError(f"Groq model '{name}' unavailable: {resp.text[:200]}")
                break

            if resp.status_code == 401:
                raise AIError("Groq rejected the API key (401). Check GROQ_API_KEY in .env.")

            if resp.status_code != 200:
                raise AIError(f"Groq request failed ({resp.status_code}): {resp.text[:300]}")

            data = resp.json()
            try:
                text = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as exc:
                raise AIError(f"Unexpected Groq response shape: {data}") from exc

            if name != _groq_working_model:
                _groq_working_model = name
                if name != config.groq_model:
                    print(f"  -> Groq model '{config.groq_model}' unavailable; using '{name}' instead.")
            return text

    hint = f" Live models for your key: {', '.join(live)}." if live else ""
    raise AIError(
        "No usable Groq model found. Set GROQ_MODEL in .env to one of the live models "
        "from https://console.groq.com/docs/models." + hint + f" Last error: {last_error}"
    )


# --------------------------------------------------------------------------- #
#  Ollama (local)
# --------------------------------------------------------------------------- #
def _ollama_generate(prompt: str) -> str:
    url = f"{config.ollama_host.rstrip('/')}/api/generate"
    try:
        resp = requests.post(
            url,
            json={"model": config.ollama_model, "prompt": prompt, "stream": False},
            timeout=600,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise AIError(
            f"Could not reach Ollama at {config.ollama_host}. Is it running? "
            "Install from https://ollama.com and run: ollama serve"
        ) from exc
    return resp.json().get("response", "")


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #
def generate_text(prompt: str) -> str:
    if config.ai_provider == "gemini":
        return _gemini_generate(prompt)
    if config.ai_provider == "groq":
        return _groq_generate(prompt)
    if config.ai_provider == "ollama":
        return _ollama_generate(prompt)
    raise AIError(f"Unknown AI_PROVIDER: {config.ai_provider}")


def generate_json(prompt: str) -> Any:
    """Ask the model for JSON and parse it defensively."""
    instruction = (
        prompt
        + "\n\nRespond with ONLY valid JSON. No commentary, no markdown fences."
    )
    raw = generate_text(instruction)
    return _extract_json(raw)