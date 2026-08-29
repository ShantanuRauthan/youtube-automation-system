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
    """Fallback transcription using local Whisper.

    Requires: pip install openai-whisper  (pulls in torch, large download).
    """
    try:
        import whisper
    except ImportError as exc:
        raise RuntimeError(
            "No YouTube transcript available and Whisper is not installed. "
            "Install it with: pip install openai-whisper"
        ) from exc

    if not os.path.exists(audio_or_video_path):
        raise FileNotFoundError(audio_or_video_path)

    model = whisper.load_model(model_size)
    result = model.transcribe(audio_or_video_path, verbose=False)

    segments: list[TranscriptSegment] = []
    for seg in result.get("segments", []):
        segments.append(
            TranscriptSegment(
                text=seg["text"].strip(),
                start=float(seg["start"]),
                duration=float(seg["end"]) - float(seg["start"]),
            )
        )
    return segments
