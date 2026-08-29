"""Build an SRT caption file from transcript timestamps, relative to a clip."""

from __future__ import annotations

import os
import textwrap

from pipeline.transcript import TranscriptSegment


def _format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_srt(
    transcript: list[TranscriptSegment],
    clip_start: float,
    clip_end: float,
    srt_path: str,
    max_line_chars: int = 34,
) -> str:
    """Write an SRT covering [clip_start, clip_end], with times rebased to 0.

    Returns the srt_path. Captions are wrapped to keep them readable on a phone.
    """
    os.makedirs(os.path.dirname(srt_path) or ".", exist_ok=True)

    entries = []
    index = 1
    for seg in transcript:
        # Keep any caption that overlaps the clip window.
        if seg.end <= clip_start or seg.start >= clip_end:
            continue

        start = max(seg.start, clip_start) - clip_start
        end = min(seg.end, clip_end) - clip_start
        if end <= start:
            continue

        text = seg.text.strip()
        if not text:
            continue
        wrapped = "\n".join(textwrap.wrap(text, width=max_line_chars)) or text

        entries.append(
            f"{index}\n{_format_timestamp(start)} --> {_format_timestamp(end)}\n{wrapped}\n"
        )
        index += 1

    with open(srt_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(entries))

    return srt_path
