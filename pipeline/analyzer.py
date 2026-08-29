"""Use AI to find the most interesting, self-contained segments in a transcript."""

from __future__ import annotations

from dataclasses import dataclass

from config import config
from pipeline.ai import generate_json
from pipeline.transcript import TranscriptSegment


@dataclass
class Segment:
    start: float
    end: float
    reason: str
    hook: str  # short punchy line describing why it's compelling

    @property
    def duration(self) -> float:
        return self.end - self.start


def _format_transcript(transcript: list[TranscriptSegment]) -> str:
    """Compact '[mm:ss] text' lines to keep the prompt small."""
    lines = []
    for seg in transcript:
        m, s = divmod(int(seg.start), 60)
        lines.append(f"[{m:02d}:{s:02d}] {seg.text}")
    return "\n".join(lines)


def find_segments(transcript: list[TranscriptSegment], video_title: str) -> list[Segment]:
    if not transcript:
        return []

    transcript_text = _format_transcript(transcript)
    total = transcript[-1].end

    prompt = f"""You are a viral short-form video editor. Below is a timestamped transcript
of a YouTube video titled "{video_title}" (total length {int(total)} seconds).

Find the {config.shorts_per_video} MOST engaging, self-contained moment(s) that would
work as a standalone vertical Short. Each moment must:
- Be between {config.min_short_seconds} and {config.max_short_seconds} seconds long.
- Start and end on a complete thought (do not cut mid-sentence).
- Contain a strong hook, surprising fact, emotional peak, or punchline.

Return a JSON array. Each element:
{{
  "start_seconds": <number>,
  "end_seconds": <number>,
  "reason": "<why this segment is compelling>",
  "hook": "<one short punchy sentence for on-screen/first-line hook>"
}}

Transcript:
{transcript_text}
"""

    data = generate_json(prompt)
    if isinstance(data, dict):
        data = data.get("segments", [])

    segments: list[Segment] = []
    for item in data:
        try:
            start = float(item["start_seconds"])
            end = float(item["end_seconds"])
        except (KeyError, TypeError, ValueError):
            continue

        # Clamp to valid bounds and enforce length limits.
        start = max(0.0, min(start, total))
        end = max(0.0, min(end, total))
        if end <= start:
            continue

        duration = end - start
        if duration < config.min_short_seconds:
            end = min(total, start + config.min_short_seconds)
        elif duration > config.max_short_seconds:
            end = start + config.max_short_seconds

        segments.append(
            Segment(
                start=start,
                end=end,
                reason=str(item.get("reason", "")),
                hook=str(item.get("hook", "")),
            )
        )

    return segments[: config.shorts_per_video]
