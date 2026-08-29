"""Free AI provider abstraction.

Supports:
  - "gemini": Google Gemini free tier (needs GEMINI_API_KEY, no cost).
  - "ollama": fully local + offline models via the Ollama server (no cost).

Both expose a single `generate_json` helper that returns parsed JSON, and a
`generate_text` helper for free-form text.
"""

from __future__ import annotations

import json
import re
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
def _gemini_generate(prompt: str) -> str:
    try:
        import google.generativeai as genai
    except ImportError as exc:  # pragma: no cover
        raise AIError("google-generativeai is not installed. Run: pip install google-generativeai") from exc

    if not config.gemini_api_key:
        raise AIError("GEMINI_API_KEY is not set.")

    genai.configure(api_key=config.gemini_api_key)
    model = genai.GenerativeModel(config.gemini_model)
    resp = model.generate_content(prompt)
    if not getattr(resp, "text", None):
        raise AIError("Gemini returned an empty response.")
    return resp.text


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
