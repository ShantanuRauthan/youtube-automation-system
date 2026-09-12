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
    "Technology": {"query": "technology explained interview", "category_id": "28"},
    "Science": {"query": "science explained documentary", "category_id": "28"},
    "Education": {"query": "educational explainer lecture", "category_id": "27"},
    "Gaming": {"query": "gaming highlights commentary", "category_id": "20"},
    "Comedy": {"query": "standup comedy clip interview", "category_id": "23"},
    "Motivation": {"query": "motivational speech podcast", "category_id": "22"},
    "Finance": {"query": "personal finance advice explained", "category_id": "25"},
    "Fitness": {"query": "fitness workout tips coach", "category_id": "17"},
    "Cooking": {"query": "cooking recipe tutorial chef", "category_id": "26"},
    "History": {"query": "history documentary explained", "category_id": "27"},
}


@dataclass
class Config:
    # Credentials
    youtube_api_key: str = field(default_factory=lambda: os.getenv("YOUTUBE_API_KEY", ""))
    client_secret_file: str = field(default_factory=lambda: os.getenv("CLIENT_SECRET_FILE", "client_secret.json"))
    token_file: str = field(default_factory=lambda: os.getenv("TOKEN_FILE", "token.json"))

    # Optional explicit path to an ffmpeg binary (bypasses PATH lookup). Useful
    # if the ffmpeg on your PATH was built without libass/drawtext and you
    # installed a full static build elsewhere. Empty = find "ffmpeg" on PATH.
    ffmpeg_binary: str = field(default_factory=lambda: os.getenv("FFMPEG_BINARY", "").strip())

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
    # Drop YouTube Music-category results. Sorting by views is heavily music-biased,
    # so this stops songs (e.g. tracks titled "Motivation") from hijacking non-music
    # categories. Set EXCLUDE_MUSIC=false only if you actually want music videos.
    exclude_music: bool = field(default_factory=lambda: _get_bool("EXCLUDE_MUSIC", True))
    min_short_seconds: int = field(default_factory=lambda: _get_int("MIN_SHORT_SECONDS", 15))
    max_short_seconds: int = field(default_factory=lambda: _get_int("MAX_SHORT_SECONDS", 60))
    # Snap AI-chosen cut points to real transcript boundaries so clips start and
    # end on complete sentences instead of mid-word. CLIP_TAIL_PAD adds a little
    # breathing room (seconds) after the last word so speech isn't clipped.
    snap_to_sentences: bool = field(default_factory=lambda: _get_bool("SNAP_TO_SENTENCES", True))
    clip_tail_pad: float = field(default_factory=lambda: float(os.getenv("CLIP_TAIL_PAD", "0.4")))
    max_source_seconds: int = field(default_factory=lambda: _get_int("MAX_SOURCE_SECONDS", 1800))
    # Only clip from source videos at least this long. Taking a 30-60s Short from
    # a short video is a near-1:1 repost (least transformative, highest claim
    # risk); requiring a longer source means the Short is genuinely a small
    # excerpt. Set to 0 to disable the floor. NOTE: not applied to a person/
    # channel search — there you explicitly asked for THAT channel's videos.
    min_source_seconds: int = field(default_factory=lambda: _get_int("MIN_SOURCE_SECONDS", 120))
    output_dir: str = field(default_factory=lambda: os.getenv("OUTPUT_DIR", "output"))

    # Sourcing: how source videos are picked.
    #   relevance = topical match, then most views (default, safest)
    #   views     = same as relevance (explicit alias)
    #   trending  = recent uploads, then highest view velocity (fast-rising)
    source_mode: str = field(default_factory=lambda: os.getenv("SOURCE_MODE", "relevance").strip().lower())
    search_pool: int = field(default_factory=lambda: _get_int("SEARCH_POOL", 50))

    # Thumbnails & packaging: grab a frame + overlay the AI hook, then set it as
    # the video's custom thumbnail on upload (needs a phone-verified channel).
    generate_thumbnail: bool = field(default_factory=lambda: _get_bool("THUMBNAILS", True))
    set_thumbnail_on_upload: bool = field(default_factory=lambda: _get_bool("SET_THUMBNAIL", True))

    # Analytics feedback loop: bias future runs toward what has performed best.
    learn_from_analytics: bool = field(default_factory=lambda: _get_bool("LEARN", True))

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

    # Face tracking: use OpenCV Haar cascade to detect and follow the speaker,
    # centering the vertical crop on them instead of just center-cropping.
    face_tracking: bool = field(default_factory=lambda: _get_bool("FACE_TRACKING", True))
    face_tracking_smoothing: float = field(default_factory=lambda: float(os.getenv("FACE_TRACKING_SMOOTHING", "0.15")))

    # Audio polish: fade the audio out over the last AUDIO_FADE_OUT seconds of
    # each Short (smoother endings), and optionally lay a royalty-free music bed
    # quietly under the clip (looped to fill the duration). Point MUSIC_BED at a
    # local audio file; MUSIC_VOLUME is 0..1 (kept low so speech stays clear).
    audio_fade_out: float = field(default_factory=lambda: float(os.getenv("AUDIO_FADE_OUT", "0.4")))
    music_bed: str = field(default_factory=lambda: os.getenv("MUSIC_BED", "").strip())
    music_volume: float = field(default_factory=lambda: float(os.getenv("MUSIC_VOLUME", "0.08")))

    # Music mode: how background music is selected.
    #   off  = no background music (default)
    #   auto = AI-selected mood-based music from the music/ directory
    #   file = use MUSIC_BED as a single track for every Short
    music_mode: str = field(default_factory=lambda: os.getenv("MUSIC_MODE", "off").strip().lower())
    # Override the mood-based selection with a specific mood (chill, happy, dark,
    # epic, sad, focus, comedy, action). Empty = auto-detect from content type.
    music_mood: str = field(default_factory=lambda: os.getenv("MUSIC_MOOD", "").strip().lower())

    # Discovery: auto-pick a trending, low-competition topic instead of choosing
    # a category by hand. Uses YouTube trending + (optionally) Google Trends.
    discovery_region: str = field(default_factory=lambda: os.getenv("DISCOVERY_REGION", "US").strip())
    discovery_pool: int = field(default_factory=lambda: _get_int("DISCOVERY_POOL", 40))
    discovery_use_trends: bool = field(default_factory=lambda: _get_bool("DISCOVERY_USE_TRENDS", True))
    # Caption rendering strategy. FFmpeg's `subtitles` filter needs libass, and
    # some builds ship without it (captions get silently skipped). This controls
    # the fallback:
    #   auto      = use FFmpeg `subtitles` if available, else render with Pillow
    #   subtitles = force FFmpeg `subtitles` (skip captions if libass is missing)
    #   pillow    = always render captions as Pillow PNG overlays (no libass needed)
    caption_mode: str = field(default_factory=lambda: os.getenv("CAPTION_MODE", "auto").strip().lower())

    # State / review workflow.
    #   review_mode = produce Shorts as "pending review" and DON'T auto-upload;
    #                 approve + upload them from the dashboard (python dashboard.py).
    #   dedup       = skip source segments already turned into a Short (safe re-runs).
    state_db: str = field(default_factory=lambda: os.getenv("STATE_DB", os.path.join(os.getenv("OUTPUT_DIR", "output"), "state.db")))
    review_mode: bool = field(default_factory=lambda: _get_bool("REVIEW_MODE", True))
    dedup: bool = field(default_factory=lambda: _get_bool("DEDUP", True))
    dashboard_port: int = field(default_factory=lambda: _get_int("DASHBOARD_PORT", 5000))

    # Long-video chunking: split transcripts >30min into overlapping windows
    # so the LLM never processes an entire 3-hour transcript at once.
    chunk_minutes: int = field(default_factory=lambda: _get_int("CHUNK_MINUTES", 20))
    chunk_overlap_seconds: int = field(default_factory=lambda: _get_int("CHUNK_OVERLAP_SECONDS", 60))

    # Voiceover commentary (the biggest transformative-use win).
    #   off  = no voiceover (default)
    #   ai   = AI writes a script, TTS speaks it
    #   file = use your own recording at VOICEOVER_FILE for every Short
    voiceover_mode: str = field(default_factory=lambda: os.getenv("VOICEOVER_MODE", "off").strip().lower())
    # Engine choices:
    #   kokoro = fully offline, 28 voices, recommended (default)
    #   edge   = Microsoft neural voices, needs internet
    #   piper  = offline, needs a .onnx voice model
    voiceover_engine: str = field(default_factory=lambda: os.getenv("VOICEOVER_ENGINE", "kokoro").strip().lower())
    voiceover_voice: str = field(default_factory=lambda: os.getenv("VOICEOVER_VOICE", "en-US-AndrewMultilingualNeural").strip())
    # Kokoro voice names: af_heart, af_alloy, af_bella, am_adam, am_echo, etc.
    kokoro_voice: str = field(default_factory=lambda: os.getenv("KOKORO_VOICE", "af_heart").strip())
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
        if self.source_mode not in {"relevance", "views", "trending"}:
            problems.append("SOURCE_MODE must be relevance, views, or trending.")
        if self.caption_mode not in {"auto", "subtitles", "pillow"}:
            problems.append("CAPTION_MODE must be auto, subtitles, or pillow.")
        if self.voiceover_mode not in {"off", "ai", "file"}:
            problems.append("VOICEOVER_MODE must be off, ai, or file.")
        if self.voiceover_engine not in {"kokoro", "edge", "piper"}:
            problems.append("VOICEOVER_ENGINE must be kokoro, edge, or piper.")
        if self.voiceover_mode == "file" and not os.path.exists(self.voiceover_file):
            problems.append(
                f"VOICEOVER_MODE=file but VOICEOVER_FILE '{self.voiceover_file}' was not found."
            )
        if self.voiceover_mode == "ai" and self.voiceover_engine == "piper" and not self.piper_model:
            problems.append("VOICEOVER_ENGINE=piper requires PIPER_MODEL (path to a .onnx voice).")
        if self.music_mode not in {"off", "auto", "file"}:
            problems.append("MUSIC_MODE must be off, auto, or file.")
        if self.music_mode == "file" and not self.music_bed:
            problems.append("MUSIC_MODE=file requires MUSIC_BED (path to an audio file).")
        if self.music_mode == "file" and self.music_bed and not os.path.exists(self.music_bed):
            problems.append(f"MUSIC_MODE=file but MUSIC_BED '{self.music_bed}' was not found.")
        return problems


config = Config()
