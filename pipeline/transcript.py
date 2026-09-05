"""Fetch a timestamped transcript for a video.

Primary source: youtube-transcript-api (fast, free, uses YouTube's own captions).
Fallback: local Whisper transcription of the downloaded audio (optional).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class TranscriptSegment:
    text: str
    start: float      # seconds from video start
    duration: float   # seconds

    @property
    def end(self) -> float:
        return self.start + self.duration


def fetch_transcript(video_id: str) -> list[TranscriptSegment]:
    """Try YouTube captions first. Returns [] if none are available."""
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        from youtube_transcript_api._errors import (
            NoTranscriptFound,
            TranscriptsDisabled,
        )
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("youtube-transcript-api is not installed.") from exc

    try:
        raw = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US", "en-GB"])
    except (NoTranscriptFound, TranscriptsDisabled):
        return []
    except Exception:
        # Any other transcript error -> let caller try the Whisper fallback.
        return []

    return [
        TranscriptSegment(text=item["text"].replace("\n", " ").strip(),
                          start=float(item["start"]),
                          duration=float(item.get("duration", 0.0)))
        for item in raw
        if item.get("text", "").strip()
    ]


def transcribe_with_whisper(audio_or_video_path: str, model_size: str = "base") -> list[TranscriptSegment]:
    """Fallback transcription of the downloaded media using a local model.

    Tries, in order:
      1. faster-whisper  (recommended: lighter, faster, no torch download)
         pip install faster-whisper
      2. openai-whisper   (the real OpenAI package)
         pip install openai-whisper   (pulls in torch, large download)

    Raises a clear error if neither is installed, or if the *wrong* PyPI
    package named `whisper` is installed (it has no `load_model`).
    """
    if not os.path.exists(audio_or_video_path):
        raise FileNotFoundError(audio_or_video_path)

    # --- Option 1: faster-whisper (preferred) ---
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        WhisperModel = None

    if WhisperModel is not None:
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
        seg_iter, _info = model.transcribe(audio_or_video_path)
        segments: list[TranscriptSegment] = []
        for seg in seg_iter:
            text = (seg.text or "").strip()
            if text:
                segments.append(
                    TranscriptSegment(
                        text=text,
                        start=float(seg.start),
                        duration=float(seg.end) - float(seg.start),
                    )
                )
        return segments

    # --- Option 2: openai-whisper ---
    try:
        import whisper
    except ImportError as exc:
        raise RuntimeError(
            "No YouTube transcript available and no local speech-to-text is installed.\n"
            "Install one of these (faster-whisper is recommended):\n"
            "    pip install faster-whisper\n"
            "  or:\n"
            "    pip install openai-whisper"
        ) from exc

    # Guard against the WRONG PyPI package also named `whisper`.
    if not hasattr(whisper, "load_model"):
        raise RuntimeError(
            "The installed `whisper` package is the WRONG one — it has no "
            "`load_model`. A different, unrelated project on PyPI is also named "
            "`whisper` and is currently shadowing OpenAI's package.\n"
            "Fix it with:\n"
            "    pip uninstall -y whisper\n"
            "    pip install openai-whisper\n"
            "  (or, lighter and recommended: pip install faster-whisper)"
        )

    model = whisper.load_model(model_size)
    result = model.transcribe(audio_or_video_path, verbose=False)

    segments = []
    for seg in result.get("segments", []):
        segments.append(
            TranscriptSegment(
                text=seg["text"].strip(),
                start=float(seg["start"]),
                duration=float(seg["end"]) - float(seg["start"]),
            )
        )
    return segments
