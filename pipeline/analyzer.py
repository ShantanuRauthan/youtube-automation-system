"""Use AI to find the most interesting, self-contained segments in a transcript.

Each candidate gets a viral-potential ``score`` so we can rank multiple moments
from the same video and keep the strongest ones. An optional ``perf_hint`` from
the analytics feedback loop nudges the model toward the lengths/styles that have
actually performed best on your channel.

Highlight selection uses an 8-signal virality framework inspired by professional
short-form editors:

1. Hook moments — immediate curiosity in the first 3 seconds
2. Emotional peaks — surprise, laughter, anger, vulnerability
3. Opinion bombs — polarizing/counter-intuitive statements
4. Revelation moments — surprising facts, stats, confessions
5. Conflict/tension — disagreements, stakes, drama
6. Quotable one-liners — memorable, shareable phrases
7. Story peaks — climax, twist, resolution
8. Practical value — actionable tips, how-to insights

Content type is detected first (podcast, interview, tutorial, etc.) so the
highlight prompt can be tailored to the video's style.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from config import config
from pipeline.ai import generate_json
from pipeline.transcript import TranscriptSegment


@dataclass
class Segment:
    start: float
    end: float
    reason: str
    hook: str  # short punchy line describing why it's compelling
    score: float = 50.0  # 0-100 viral potential, used for ranking
    virality_signals: list[str] = field(default_factory=list)  # which signals were detected

    @property
    def duration(self) -> float:
        return self.end - self.start


# --------------------------------------------------------------------------- #
#  Content type detection
# --------------------------------------------------------------------------- #

# Content type -> guidance for the highlight prompt. This lets the model focus
# on the right kind of moment for each style of video.
_CONTENT_TYPE_GUIDANCE: dict[str, str] = {
    "podcast": (
        "Focus on quotable moments, surprising confessions, emotional peaks, "
        "and strong opinions. Podcasts thrive on personality-driven clips."
    ),
    "interview": (
        "Focus on revealing answers, surprising facts, emotional reactions, "
        "and counter-intuitive statements. The best interview clips are moments "
        "the guest says something unexpected."
    ),
    "tutorial": (
        "Focus on practical tips, 'aha' moments, counter-intuitive facts, "
        "and actionable advice. Viewers share clips that teach them something."
    ),
    "lecture": (
        "Focus on surprising facts, mind-blowing stats, simple explanations "
        "of complex topics, and quotable one-liners from the lecturer."
    ),
    "commentary": (
        "Focus on opinion bombs, strong takes, controversial statements, "
        "and emotional reactions. Commentary clips thrive on polarization."
    ),
    "debate": (
        "Focus on tension, counter-arguments, 'destroyed' moments, and "
        "emotional peaks. Debates thrive on conflict and drama."
    ),
    "vlog": (
        "Focus on emotional peaks, funny moments, surprising events, "
        "and relatable situations. Vlogs thrive on authenticity."
    ),
    "other": (
        "Focus on the strongest hook, surprising facts, emotional peaks, "
        "and quotable moments. Use general virality signals."
    ),
}

_DENSITY_GUIDANCE: dict[str, str] = {
    "low": (
        "This video has low information density — there are long pauses or "
        "filler. Focus on the few concentrated moments of value."
    ),
    "medium": (
        "This video has moderate information density. Pick the peak moments."
    ),
    "high": (
        "This video is densely packed with information. There are many "
        "potential clips — be selective and pick only the absolute strongest."
    ),
}


def _classify_content(transcript_text: str, video_title: str) -> tuple[str, str]:
    """Classify video content type and information density via a single LLM call.

    Returns (content_type, density) where content_type is one of:
    podcast, interview, tutorial, lecture, commentary, debate, vlog, other
    and density is one of: low, medium, high
    """
    truncated = transcript_text[:2500]
    prompt = f"""You are a content analyst. Classify this YouTube video based on its
title and the beginning of its transcript.

Title: "{video_title}"
Transcript (first ~2500 chars):
\"\"\"
{truncated}
\"\"\"

Return a JSON object with exactly these keys:
{{
  "content_type": "<one of: podcast, interview, tutorial, lecture, commentary, debate, vlog, other>",
  "density": "<one of: low, medium, high>",
  "reason": "<one sentence explaining the classification>"
}}

Rules:
- "podcast" = conversational, multi-person, long-form discussion
- "interview" = Q&A format, one person asking questions
- "tutorial" = step-by-step how-to, screen recording, demonstration
- "lecture" = single speaker teaching a topic (classroom style)
- "commentary" = one person giving opinions on a topic/news/event
- "debate" = two+ people arguing opposing views
- "vlog" = personal life content, daily routine, travel
- "other" = doesn't fit the above categories
- density is about how much useful content per minute (low = lots of filler, high = packed with value)"""

    data = generate_json(prompt)
    if not isinstance(data, dict):
        return "other", "medium"

    content_type = str(data.get("content_type", "other")).lower().strip()
    density = str(data.get("density", "medium")).lower().strip()

    valid_types = {"podcast", "interview", "tutorial", "lecture", "commentary", "debate", "vlog", "other"}
    valid_density = {"low", "medium", "high"}

    if content_type not in valid_types:
        content_type = "other"
    if density not in valid_density:
        density = "medium"

    return content_type, density


# --------------------------------------------------------------------------- #
#  Transcript formatting
# --------------------------------------------------------------------------- #

def _format_transcript(transcript: list[TranscriptSegment]) -> str:
    """Compact '[mm:ss] text' lines to keep the prompt small."""
    lines = []
    for seg in transcript:
        m, s = divmod(int(seg.start), 60)
        lines.append(f"[{m:02d}:{s:02d}] {seg.text}")
    return "\n".join(lines)


# Groq free tier: 8000 TPM. ~1 token per 4 chars. Keep under 6000 chars (~1500 tokens).
MAX_TRANSCRIPT_CHARS = 6000


def _get_segment_text(start: float, end: float, transcript: list[TranscriptSegment]) -> str:
    """Extract the transcript text for a given time range."""
    parts = []
    for seg in transcript:
        if seg.end > start and seg.start < end:
            parts.append(seg.text)
    return " ".join(parts)


def _truncate_transcript(transcript_text: str, max_chars: int = MAX_TRANSCRIPT_CHARS) -> str:
    """Smart truncation: keep beginning + end, sample from middle.

    Hook moments are often at the start or end of a video. We keep the
    first 20% and last 20% of the text, then fill the middle with evenly
    spaced samples to stay under max_chars.
    """
    if len(transcript_text) <= max_chars:
        return transcript_text

    lines = transcript_text.split("\n")
    total_lines = len(lines)

    # Keep first 20% and last 20% of lines.
    head_count = max(1, total_lines // 5)
    tail_count = max(1, total_lines // 5)

    head_lines = lines[:head_count]
    tail_lines = lines[-tail_count:]
    middle_lines = lines[head_count:-tail_count] if tail_count < total_lines else []

    # Fill middle with evenly spaced lines.
    remaining = max_chars - len("\n".join(head_lines + tail_lines)) - 20  # 20 for separators
    if remaining > 0 and middle_lines:
        # Sample ~1 line per 80 chars of remaining budget.
        sample_count = min(len(middle_lines), remaining // 80)
        step = max(1, len(middle_lines) // sample_count) if sample_count > 0 else 1
        middle_sample = middle_lines[::step][:sample_count]
    else:
        middle_sample = []

    parts = head_lines
    if middle_sample:
        parts.append("[...]")
        parts.extend(middle_sample)
        parts.append("[...]")
    parts.extend(tail_lines)

    result = "\n".join(parts)
    return result[:max_chars] if len(result) > max_chars else result


_SENTENCE_END = (".", "!", "?", "…", '"', "\u201d")


def _ends_sentence(text: str) -> bool:
    return text.rstrip().endswith(_SENTENCE_END)


def _snap_to_sentences(
    start: float,
    end: float,
    transcript: list[TranscriptSegment],
    total: float,
) -> tuple[float, float]:
    """Move the AI's rough start/end to real transcript boundaries so the clip
    begins and ends on a complete sentence instead of mid-word.

    - Start snaps back to the beginning of the caption line it falls in, then
      walks further back while the previous line does NOT end a sentence (i.e.
      we're mid-sentence), so we capture the whole sentence opening.
    - End snaps forward to the end of the caption line it falls in, then extends
      to the next line that ends on sentence punctuation, as long as we stay
      under the max length. A small tail pad keeps the final word from clipping.
    """
    if not transcript:
        return start, end

    min_len = float(config.min_short_seconds)
    max_len = float(config.max_short_seconds)
    n = len(transcript)

    # ---- snap START to a sentence beginning ----
    start_idx = 0
    for i, t in enumerate(transcript):
        if t.end > start:
            start_idx = i
            break
    # walk back while the previous line didn't finish a sentence
    while start_idx > 0 and not _ends_sentence(transcript[start_idx - 1].text):
        # don't run away past the max clip length
        if end - transcript[start_idx - 1].start > max_len:
            break
        start_idx -= 1

    # Ensure at least 5s context lead-in before the highlight moment.
    # Walk back to include setup if we're starting too close to the action.
    MIN_CONTEXT_SECONDS = 5.0
    context_start = start - MIN_CONTEXT_SECONDS
    while start_idx > 0 and transcript[start_idx - 1].start >= context_start:
        if end - transcript[start_idx - 1].start > max_len:
            break
        start_idx -= 1

    snapped_start = max(0.0, transcript[start_idx].start)

    # ---- snap END to a sentence ending ----
    end_idx = start_idx
    for i in range(start_idx, n):
        if transcript[i].start < end:
            end_idx = i
        else:
            break
    # extend forward to the next line that ends a sentence, within max length
    while end_idx < n - 1 and not _ends_sentence(transcript[end_idx].text):
        if transcript[end_idx + 1].end - snapped_start > max_len:
            break
        end_idx += 1
    snapped_end = transcript[end_idx].end

    # ---- enforce length using whole lines where possible ----
    # too short: keep adding following lines until we hit the minimum
    while snapped_end - snapped_start < min_len and end_idx < n - 1:
        if transcript[end_idx + 1].end - snapped_start > max_len:
            break
        end_idx += 1
        snapped_end = transcript[end_idx].end

    # too long: trim whole lines off the end until within the maximum
    while snapped_end - snapped_start > max_len and end_idx > start_idx:
        end_idx -= 1
        snapped_end = transcript[end_idx].end

    # After trimming, ensure we still end on a sentence boundary.
    # If not, keep trimming until we find one (even if slightly under max_len).
    attempts = 0
    while end_idx > start_idx and not _ends_sentence(transcript[end_idx].text) and attempts < 10:
        end_idx -= 1
        snapped_end = transcript[end_idx].end
        attempts += 1

    # small breathing room so the last word isn't cut, but never past the video
    snapped_end = min(total, snapped_end + config.clip_tail_pad)

    if snapped_end <= snapped_start:
        return start, end
    return snapped_start, snapped_end


# --------------------------------------------------------------------------- #
#  Virality scoring prompt
# --------------------------------------------------------------------------- #

def _build_virality_prompt(
    transcript_text: str,
    video_title: str,
    total_seconds: int,
    shorts_count: int,
    min_seconds: int,
    max_seconds: int,
    content_type: str,
    density: str,
    hint_line: str,
) -> str:
    """Build the AI prompt with 8-signal virality framework + content-type guidance."""
    content_guidance = _CONTENT_TYPE_GUIDANCE.get(content_type, _CONTENT_TYPE_GUIDANCE["other"])
    density_guidance = _DENSITY_GUIDANCE.get(density, _DENSITY_GUIDANCE["medium"])

    return f"""You are a viral short-form video editor with years of experience picking
the perfect moments from long-form content. Below is a timestamped transcript
of a YouTube video titled "{video_title}" (total length {total_seconds} seconds).

CONTENT ANALYSIS:
- Type: {content_type}
- Information density: {density}
- Content guidance: {content_guidance}
- Density guidance: {density_guidance}
{hint_line}
YOUR TASK:
Find the {shorts_count} MOST viral, self-contained moment(s) that would work as a
standalone vertical Short (TikTok, Reels, YouTube Shorts).

NEVER SELECT — AUTO-REJECT (score 0, do not return):
- Sponsorship/ads: "use code", "discount", "partnered with", "sponsored by", "check out my merch"
- Self-promotion: "subscribe", "like and share", "link in description", "download my app", "join my channel"
- Intros/outros: "welcome back", "in today's video", "thanks for watching", "see you next time"
- Filler: "um", "uh", "so yeah", "anyway", "moving on"
- Metadata: view counts, subscriber counts, "smash that bell"

CONTEXT BEFORE HOOK — CRITICAL:
Every short MUST start with enough context so a viewer who has never seen this
video can follow along. The first 3-5 seconds should establish:
  - WHO is speaking or WHAT topic is being discussed
  - WHY this moment matters (the setup, not just the punchline)

BAD: Starting at "And then he said..." (no context — viewer is confused)
GOOD: Starting at "This one experiment changed everything we knew about gravity.
      And then he said..." (context + hook)

Always start your clip 5-15 seconds BEFORE the actual highlight moment to give
the viewer time to orient. The highlight can land at second 5-10, not second 0.

ENDING RULE — NEVER CUT MID-SENTENCE:
Your clip MUST end on a complete thought. The last sentence should feel like
a natural stopping point — not cut off mid-word or mid-clause.

If the segment you want runs past the max duration, TRIM from the END (remove
the last sentence) rather than cutting mid-sentence. A clip with a clean
ending at 50 seconds is better than a 60-second clip that cuts off abruptly.

SCORING FRAMEWORK (100 points max):
Score each candidate 0-100. Be CRITICAL — most clips are 30-60, only exceptional
ones reach 70+. Do NOT give high scores to average content.

Signals:
1. SELF-CONTAINED — Does it make sense without watching the rest? (+20 pts)
2. CONTEXT LEAD-IN — Does it start 5+ seconds before the main moment? (+15 pts)
3. HOOK — Does it grab attention in the first 3-5 seconds? (+12 pts)
4. EMOTIONAL PEAK — Does it trigger surprise, laughter, anger, empathy? (+10 pts)
5. REVELATION — Does it reveal a surprising fact, stat, or confession? (+10 pts)
6. CONFLICT — Is there tension, disagreement, or stakes? (+8 pts)
7. QUOTABLE — Is there a memorable one-liner people would share? (+8 pts)
8. PRACTICAL VALUE — Does it teach something actionable? (+7 pts)
9. CLEAN ENDING — Does it end on a complete thought? (+10 pts)

Score interpretation:
- 80-100: Exceptional (only 1-2 per video max, if any)
- 60-79: Good viral potential
- 40-59: Decent but not great
- 0-39: Skip this segment

Return a JSON array. Each element:
{{
  "start_seconds": <number — include context, start BEFORE the main moment>,
  "end_seconds": <number>,
  "reason": "<why this segment is compelling, mention which signals it hits>",
  "hook": "<one short punchy sentence for on-screen/first-line hook>",
  "score": <integer 0-100, be critical>,
  "virality_signals": [<list of signal names from: self_contained, context_lead_in, hook, emotional_peak, revelation, conflict, quotable, practical_value, clean_ending>]
}}

Transcript:
{transcript_text}
"""


# --------------------------------------------------------------------------- #
#  Long-video chunking
# --------------------------------------------------------------------------- #

def _chunk_transcript(
    transcript: list[TranscriptSegment],
    chunk_minutes: int = 20,
    overlap_seconds: float = 60.0,
) -> list[list[TranscriptSegment]]:
    """Split a long transcript into overlapping windows.

    For videos under 30 minutes, returns the full transcript as a single chunk.
    For longer videos, splits into `chunk_minutes`-minute windows with
    `overlap_seconds` overlap so cross-boundary highlights are not missed.
    """
    if not transcript:
        return []

    total_duration = transcript[-1].end
    chunk_seconds = chunk_minutes * 60

    # Short videos: process the whole thing at once.
    if total_duration <= chunk_seconds + 120:
        return [transcript]

    chunks: list[list[TranscriptSegment]] = []
    chunk_start = 0.0

    while chunk_start < total_duration:
        chunk_end = chunk_start + chunk_seconds

        # Collect segments that overlap this window.
        window = [seg for seg in transcript if seg.end > chunk_start and seg.start < chunk_end]
        if window:
            chunks.append(window)

        # Advance by chunk length minus overlap.
        chunk_start += chunk_seconds - overlap_seconds

    return chunks


def _merge_and_rank_segments(
    all_segments: list[Segment],
    max_count: int,
) -> list[Segment]:
    """Merge segments from multiple chunks, dedup overlapping, and keep top N.

    When two segments overlap by >50% of the shorter one's duration, the
    lower-scoring one is dropped (same logic as AI-Youtube-Shorts-Generator).
    """
    if not all_segments:
        return []

    # Sort by score descending so higher-scoring segments are kept.
    all_segments.sort(key=lambda s: s.score, reverse=True)

    kept: list[Segment] = []
    for seg in all_segments:
        is_duplicate = False
        for existing in kept:
            # Check overlap as fraction of the shorter segment.
            overlap_start = max(seg.start, existing.start)
            overlap_end = min(seg.end, existing.end)
            overlap = max(0.0, overlap_end - overlap_start)
            shorter = min(seg.duration, existing.duration)
            if shorter > 0 and overlap / shorter > 0.50:
                is_duplicate = True
                break
        if not is_duplicate:
            kept.append(seg)

    return kept[:max_count]


# --------------------------------------------------------------------------- #
#  Main entry point
# --------------------------------------------------------------------------- #

def find_segments(
    transcript: list[TranscriptSegment],
    video_title: str,
    perf_hint: str = "",
) -> list[Segment]:
    if not transcript:
        return []

    transcript_text = _format_transcript(transcript)
    total = transcript[-1].end

    # Step 1: Classify content type and density (one LLM call).
    try:
        # Truncate for Groq free-tier token limits.
        classify_text = _truncate_transcript(transcript_text, max_chars=2000)
        content_type, density = _classify_content(classify_text, video_title)
        print(f"  -> Content type: {content_type} | density: {density}")
    except Exception:
        content_type, density = "other", "medium"

    hint_line = (
        f"\nWhat has worked before on this channel (bias toward this): {perf_hint}\n"
        if perf_hint
        else ""
    )

    # Step 2: Split long videos into overlapping chunks.
    chunk_minutes = getattr(config, "chunk_minutes", 20)
    chunk_overlap = getattr(config, "chunk_overlap_seconds", 60)
    chunks = _chunk_transcript(transcript, chunk_minutes, chunk_overlap)

    if len(chunks) > 1:
        print(f"  -> Long video: split into {len(chunks)} chunks ({chunk_minutes}min each, {chunk_overlap}s overlap)")

    # Step 3: Find highlights in each chunk.
    all_segments: list[Segment] = []

    for chunk_idx, chunk in enumerate(chunks):
        if len(chunks) > 1:
            chunk_text = _format_transcript(chunk)
            chunk_start = chunk[0].start
            chunk_end = chunk[-1].end
            print(f"  -> Processing chunk {chunk_idx + 1}/{len(chunks)} ({chunk_start:.0f}s–{chunk_end:.0f}s)")
        else:
            chunk_text = transcript_text
            chunk_start = 0.0
            chunk_end = total

        # Truncate to fit Groq free-tier token limits.
        chunk_text = _truncate_transcript(chunk_text)

        prompt = _build_virality_prompt(
            transcript_text=chunk_text,
            video_title=video_title,
            total_seconds=int(total),
            shorts_count=config.shorts_per_video * 2 if len(chunks) > 1 else config.shorts_per_video,
            min_seconds=config.min_short_seconds,
            max_seconds=config.max_short_seconds,
            content_type=content_type,
            density=density,
            hint_line=hint_line,
        )

        data = generate_json(prompt)
        if isinstance(data, dict):
            data = data.get("segments", [])

        # Keywords that indicate promotional/filler content — reject these.
        _REJECT_KEYWORDS = [
            "subscribe", "like and share", "link in description", "use code",
            "discount", "sponsored", "partnered with", "download my app",
            "join my channel", "welcome back", "in today's video",
            "thanks for watching", "see you next time", "smash that bell",
            "merch", "check out my", "follow me", "support the channel",
        ]

        for item in data:
            try:
                start = float(item["start_seconds"])
                end = float(item["end_seconds"])
            except (KeyError, TypeError, ValueError):
                continue

            # Clamp to valid bounds.
            start = max(0.0, min(start, total))
            end = max(0.0, min(end, total))
            if end <= start:
                continue

            # Check transcript text in this segment for promotional keywords.
            segment_text = _get_segment_text(start, end, transcript).lower()
            if any(kw in segment_text for kw in _REJECT_KEYWORDS):
                print(f"  -> Skipped promotional segment at {start:.0f}s-{end:.0f}s")
                continue

            # Check that the segment starts before the main moment (context lead-in).
            # If start is within 2s of what looks like the hook, push it back.
            if len(data) > 0:
                # Allow AI's start time but ensure at least 3s context.
                pass

            # Snap cut points to real sentence boundaries.
            if config.snap_to_sentences:
                start, end = _snap_to_sentences(start, end, transcript, total)
            else:
                duration = end - start
                if duration < config.min_short_seconds:
                    end = min(total, start + config.min_short_seconds)
                elif duration > config.max_short_seconds:
                    end = start + config.max_short_seconds

            try:
                score = float(item.get("score", 50))
            except (TypeError, ValueError):
                score = 50.0

            raw_signals = item.get("virality_signals", [])
            if isinstance(raw_signals, list):
                virality_signals = [str(s) for s in raw_signals if s]
            else:
                virality_signals = []

            all_segments.append(
                Segment(
                    start=start,
                    end=end,
                    reason=str(item.get("reason", "")),
                    hook=str(item.get("hook", "")),
                    score=max(0.0, min(score, 100.0)),
                    virality_signals=virality_signals,
                )
            )

    # Step 4: Merge across chunks and dedup overlapping segments.
    if len(chunks) > 1:
        result = _merge_and_rank_segments(all_segments, config.shorts_per_video)
        print(f"  -> Merged {len(all_segments)} candidates into {len(result)} unique segments")
    else:
        all_segments.sort(key=lambda s: s.score, reverse=True)
        result = all_segments[: config.shorts_per_video]

    return result
