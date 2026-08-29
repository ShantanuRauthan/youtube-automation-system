"""Build caption files from transcript timestamps, relative to a clip.

Two flavors:

* ``build_srt`` — plain SubRip captions (the original, still used when
  ``KARAOKE_CAPTIONS=false``).
* ``build_ass`` — Advanced SubStation Alpha with **karaoke** timing, so words
  highlight one-by-one as they're spoken. This is the animated, "TikTok-style"
  caption that noticeably lifts watch time on Shorts.

``keyword_times`` extracts a handful of emphasis moments (numbers, strong
words, questions) used by the editor to trigger brief punch-in zooms.
"""

from __future__ import annotations

import os
import re
import textwrap

from pipeline.transcript import TranscriptSegment

# ASS colours are &HAABBGGRR (alpha, blue, green, red).
_WHITE = "&H00FFFFFF"        # spoken-but-not-yet-highlighted
_HIGHLIGHT = "&H0000FFFF"    # active word sweep (yellow)
_OUTLINE = "&H00000000"      # black outline
_SHADOW = "&H64000000"       # semi-transparent shadow

_EMPHASIS = re.compile(
    r"\b(never|always|best|worst|secret|most|first|huge|crazy|shocking|"
    r"actually|literally|insane|proven|nobody|everyone|billion|million)\b",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
#  SRT (plain)
# --------------------------------------------------------------------------- #
def _srt_ts(seconds: float) -> str:
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
    """Write an SRT covering [clip_start, clip_end], with times rebased to 0."""
    os.makedirs(os.path.dirname(srt_path) or ".", exist_ok=True)

    entries = []
    index = 1
    for seg in transcript:
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
            f"{index}\n{_srt_ts(start)} --> {_srt_ts(end)}\n{wrapped}\n"
        )
        index += 1

    with open(srt_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(entries))
    return srt_path


# --------------------------------------------------------------------------- #
#  ASS (karaoke)
# --------------------------------------------------------------------------- #
def _ass_ts(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    cs = int(round(seconds * 100))
    hours, cs = divmod(cs, 360_000)
    minutes, cs = divmod(cs, 6_000)
    secs, cs = divmod(cs, 100)
    return f"{hours:d}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _ass_header() -> str:
    # Alignment 2 = bottom-centre; MarginV lifts captions above the watermark.
    style = (
        "Style: Pop,Arial Black,58,"
        f"{_HIGHLIGHT},{_WHITE},{_OUTLINE},{_SHADOW},"
        "-1,0,0,0,100,100,0,0,1,4,1,2,80,80,300,1"
    )
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        f"{style}\n\n"
        "[Events]\n"
        "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )


def _ass_escape(text: str) -> str:
    return text.replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass(
    transcript: list[TranscriptSegment],
    clip_start: float,
    clip_end: float,
    ass_path: str,
    words_per_line: int = 4,
) -> str:
    """Write a karaoke .ass caption file rebased to the clip window.

    Word timings are estimated by distributing each transcript segment's
    duration across its words (weighted by length), then grouped into short
    lines. Each word gets a ``\\kf`` sweep so it highlights as it's spoken.
    """
    os.makedirs(os.path.dirname(ass_path) or ".", exist_ok=True)
    clip_dur = max(0.1, clip_end - clip_start)

    events: list[str] = []
    for seg in transcript:
        if seg.end <= clip_start or seg.start >= clip_end:
            continue

        seg_start = max(seg.start, clip_start) - clip_start
        seg_end = min(seg.end, clip_end) - clip_start
        seg_start = max(0.0, min(seg_start, clip_dur))
        seg_end = max(0.0, min(seg_end, clip_dur))
        if seg_end <= seg_start:
            continue

        words = [w for w in seg.text.split() if w.strip()]
        if not words:
            continue

        span = seg_end - seg_start
        weights = [len(w) + 1 for w in words]
        total_w = sum(weights) or 1
        durations = [span * w / total_w for w in weights]  # seconds per word

        # Group words into short on-screen lines.
        cursor = seg_start
        for i in range(0, len(words), words_per_line):
            chunk_words = words[i : i + words_per_line]
            chunk_durs = durations[i : i + words_per_line]
            line_start = cursor
            karaoke_parts = []
            for word, dur in zip(chunk_words, chunk_durs):
                cs = max(1, int(round(dur * 100)))  # centiseconds
                karaoke_parts.append(f"{{\\kf{cs}}}{_ass_escape(word)} ")
                cursor += dur
            line_end = cursor
            text = "".join(karaoke_parts).strip()
            events.append(
                f"Dialogue: 0,{_ass_ts(line_start)},{_ass_ts(line_end)},Pop,,0,0,0,,{text}"
            )

    with open(ass_path, "w", encoding="utf-8") as fh:
        fh.write(_ass_header())
        fh.write("\n".join(events))
        fh.write("\n")
    return ass_path


# --------------------------------------------------------------------------- #
#  Keyword / emphasis moments (for punch-in zooms)
# --------------------------------------------------------------------------- #
def keyword_times(
    transcript: list[TranscriptSegment],
    clip_start: float,
    clip_end: float,
    max_points: int = 6,
    min_gap: float = 2.0,
) -> list[float]:
    """Return clip-relative timestamps of emphasized moments.

    Heuristic score per transcript line: numbers, '!'/'?', long words, and a
    small set of emphasis words. Points are spaced at least ``min_gap`` apart.
    """
    scored: list[tuple[float, int]] = []
    for seg in transcript:
        if seg.end <= clip_start or seg.start >= clip_end:
            continue
        t = max(seg.start, clip_start) - clip_start
        text = seg.text
        score = 0
        if re.search(r"\d", text):
            score += 2
        if re.search(r"[!?]", text):
            score += 1
        if any(len(w) >= 8 for w in text.split()):
            score += 1
        if _EMPHASIS.search(text):
            score += 2
        if score > 0:
            scored.append((t, score))

    scored.sort(key=lambda x: x[0])
    picked: list[float] = []
    for t, _score in scored:
        if picked and (t - picked[-1]) < min_gap:
            continue
        picked.append(round(t, 2))
        if len(picked) >= max_points:
            break
    return picked
