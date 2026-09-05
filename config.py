"""Central configuration + category presets loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _get_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "y", "on"}


def _get_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


# YouTube "video category IDs" used when uploading (US region).
# The keys are the friendly categories shown in the CLI menu.
CATEGORIES: dict[str, dict] = {
    "Technology": {"query": "technology explained", "category_id": "28"},
    "Science": {"query": "science facts", "category_id": "28"},
    "Education": {"query": "educational explainer", "category_id": "27"},
    "Gaming": {"query": "gaming highlights", "category_id": "20"},
    "Comedy": {"query": "funny moments", "category_id": "23"},
    "Motivation": {"query": "motivational speech", "category_id": "22"},
    "Finance": {"query": "personal finance tips", "category_id": "25"},
    "Fitness": {"query": "fitness workout tips", "category_id": "17"},
    "Cooking": {"query": "cooking recipe", "category_id": "26"},
    "History": {"query": "history documentary", "category_id": "27"},
}


@dataclass
class Config:
    # Credentials
    youtube_api_key: str = field(default_factory=lambda: os.getenv("YOUTUBE_API_KEY", ""))
    client_secret_file: str = field(default_factory=lambda: os.getenv("CLIENT_SECRET_FILE", "client_secret.json"))
    token_file: str = field(default_factory=lambda: os.getenv("TOKEN_FILE", "token.json"))

    # AI provider
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "gemini").strip().lower())
    gemini_api_key: str = field(default_factory=lambda: os.getenv("GEMINI_API_KEY", ""))
    gemini_model: str = field(default_factory=lambda: os.getenv("GEMINI_MODEL", "gemini-flash-latest"))
    ollama_host: str = field(default_factory=lambda: os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("OLLAMA_MODEL", "llama3.1"))
    # Groq: free, fast, hosted open-source models (Llama etc). OpenAI-compatible API.
    # Get a free key at https://console.groq.com/keys — far higher limits than Gemini free tier.
    groq_api_key: str = field(default_factory=lambda: os.getenv("GROQ_API_KEY", ""))
    groq_model: str = field(default_factory=lambda: os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"))

    # Behaviour
    auto_upload: bool = field(default_factory=lambda: _get_bool("AUTO_UPLOAD", True))
    upload_privacy: str = field(default_factory=lambda: os.getenv("UPLOAD_PRIVACY", "private").strip().lower())
    max_videos: int = field(default_factory=lambda: _get_int("MAX_VIDEOS", 3))
    shorts_per_video: int = field(default_factory=lambda: _get_int("SHORTS_PER_VIDEO", 1))
    min_views: int = field(default_factory=lambda: _get_int("MIN_VIEWS", 100_000))
    min_short_seconds: int = field(default_factory=lambda: _get_int("MIN_SHORT_SECONDS", 15))
    max_short_seconds: int = field(default_factory=lambda: _get_int("MAX_SHORT_SECONDS", 60))
    max_source_seconds: int = field(default_factory=lambda: _get_int("MAX_SOURCE_SECONDS", 1800))
    output_dir: str = field(default_factory=lambda: os.getenv("OUTPUT_DIR", "output"))

    # Branding / transformation (helps make Shorts transformative, not a Content ID bypass)
    brand_handle: str = field(default_factory=lambda: os.getenv("BRAND_HANDLE", "").strip())
    show_header_bar: bool = field(default_factory=lambda: _get_bool("SHOW_HEADER_BAR", True))
    show_watermark: bool = field(default_factory=lambda: _get_bool("SHOW_WATERMARK", True))
    reframe_zoom: float = field(default_factory=lambda: float(os.getenv("REFRAME_ZOOM", "1.06")))
    credit_source: bool = field(default_factory=lambda: _get_bool("CREDIT_SOURCE", True))

    # Animated captions + motion.
    #   karaoke_captions = word-by-word highlighted (ASS) captions instead of plain SRT
    #   keyword_zoom     = brief punch-in zooms on emphasized moments (numbers, strong words)
    karaoke_captions: bool = field(default_factory=lambda: _get_bool("KARAOKE_CAPTIONS", True))
    keyword_zoom: bool = field(default_factory=lambda: _get_bool("KEYWORD_ZOOM", True))
    keyword_zoom_intensity: float = field(default_factory=lambda: float(os.getenv("KEYWORD_ZOOM_INTENSITY", "0.12")))

    # State / review workflow.
    #   review_mode = produce Shorts as "pending review" and DON'T auto-upload;
    #                 approve + upload them from the dashboard (python dashboard.py).
    #   dedup       = skip source segments already turned into a Short (safe re-runs).
    state_db: str = field(default_factory=lambda: os.getenv("STATE_DB", os.path.join(os.getenv("OUTPUT_DIR", "output"), "state.db")))
    review_mode: bool = field(default_factory=lambda: _get_bool("REVIEW_MODE", True))
    dedup: bool = field(default_factory=lambda: _get_bool("DEDUP", True))
    dashboard_port: int = field(default_factory=lambda: _get_int("DASHBOARD_PORT", 5000))

    # Voiceover commentary (the biggest transformative-use win).
    #   off  = no voiceover (default)
    #   ai   = AI writes a script, TTS speaks it
    #   file = use your own recording at VOICEOVER_FILE for every Short
    voiceover_mode: str = field(default_factory=lambda: os.getenv("VOICEOVER_MODE", "off").strip().lower())
    voiceover_engine: str = field(default_factory=lambda: os.getenv("VOICEOVER_ENGINE", "edge").strip().lower())
    voiceover_voice: str = field(default_factory=lambda: os.getenv("VOICEOVER_VOICE", "en-US-AndrewMultilingualNeural").strip())
    voiceover_file: str = field(default_factory=lambda: os.getenv("VOICEOVER_FILE", "").strip())
    piper_model: str = field(default_factory=lambda: os.getenv("PIPER_MODEL", "").strip())
    duck_volume: float = field(default_factory=lambda: float(os.getenv("DUCK_VOLUME", "0.15")))

    def validate(self) -> list[str]:
        """Return a list of human-readable configuration problems."""
        problems: list[str] = []
        if not self.youtube_api_key:
            problems.append("YOUTUBE_API_KEY is not set (needed to search videos).")
        if self.ai_provider == "gemini" and not self.gemini_api_key:
            problems.append("AI_PROVIDER=gemini but GEMINI_API_KEY is not set.")
        if self.ai_provider == "groq" and not self.groq_api_key:
            problems.append("AI_PROVIDER=groq but GROQ_API_KEY is not set (get one free at https://console.groq.com/keys).")
        if self.ai_provider not in {"gemini", "groq", "ollama"}:
            problems.append(f"AI_PROVIDER must be 'gemini', 'groq', or 'ollama', got '{self.ai_provider}'.")
        if self.auto_upload and not os.path.exists(self.client_secret_file):
            problems.append(
                f"AUTO_UPLOAD=true but OAuth file '{self.client_secret_file}' was not found. "
                "Download a Desktop OAuth client from Google Cloud Console."
            )
        if self.upload_privacy not in {"private", "unlisted", "public"}:
            problems.append("UPLOAD_PRIVACY must be private, unlisted, or public.")
        if self.voiceover_mode not in {"off", "ai", "file"}:
            problems.append("VOICEOVER_MODE must be off, ai, or file.")
        if self.voiceover_engine not in {"edge", "piper"}:
            problems.append("VOICEOVER_ENGINE must be edge or piper.")
        if self.voiceover_mode == "file" and not os.path.exists(self.voiceover_file):
            problems.append(
                f"VOICEOVER_MODE=file but VOICEOVER_FILE '{self.voiceover_file}' was not found."
            )
        if self.voiceover_mode == "ai" and self.voiceover_engine == "piper" and not self.piper_model:
            problems.append("VOICEOVER_ENGINE=piper requires PIPER_MODEL (path to a .onnx voice).")
        return problems


config = Config()