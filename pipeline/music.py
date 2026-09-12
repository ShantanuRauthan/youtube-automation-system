"""Background music selection and management.

Provides mood-based music selection from a local library of royalty-free
tracks. Music is laid quietly under each Short to add atmosphere without
distracting from the speech.

Music sources (all royalty-free, no attribution required):
  - Pixabay Music (https://pixabay.com/music/) — CC0 license
  - Free Music Archive (https://freemusicarchive.org/) — CC0/CC-BY
  - YouTube Audio Library — free for YT creators

Usage:
  1. Drop MP3 files into the `music/` directory at the project root.
  2. Name them descriptively: `chill-lofi-beat.mp3`, `epic-cinematic.mp3`, etc.
  3. The system auto-detects mood from filename keywords and the Short's content.
  4. Set MUSIC_MODE=auto in .env to enable automatic background music.

Mood categories:
  chill    — relaxed, lo-fi, ambient, calm
  happy    — upbeat, positive, energetic, fun
  dark     — suspenseful, mysterious, tense, eerie
  epic     — cinematic, dramatic, powerful, intense
  sad      — melancholic, emotional, reflective, somber
  focus    — minimal, study, concentration, clean
  comedy   — quirky, playful, funny, lighthearted
  action   — fast-paced, energetic, workout, sports
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass
from typing import Optional

from config import config


# Keyword -> mood mapping. Filenames containing these keywords are auto-tagged.
_MOOD_KEYWORDS: dict[str, list[str]] = {
    "chill": ["chill", "lofi", "lo-fi", "ambient", "calm", "relax", "smooth", "mellow", "downtempo"],
    "happy": ["happy", "upbeat", "positive", "fun", "bright", "sunny", "feel-good", "joyful", "pop"],
    "dark": ["dark", "suspense", "mystery", "tense", "eerie", "creepy", "horror", "shadow", "noir"],
    "epic": ["epic", "cinematic", "dramatic", "powerful", "intense", "orchestral", "trailer", "hero"],
    "sad": ["sad", "melancholy", "emotional", "reflective", "somber", "piano", "gentle", "bittersweet"],
    "focus": ["focus", "minimal", "study", "concentration", "clean", "simple", "tech", "corporate"],
    "comedy": ["comedy", "funny", "quirky", "playful", "lighthearted", "goofy", "silly", "cartoon"],
    "action": ["action", "fast", "energetic", "workout", "sports", "driving", "pump", "adrenaline"],
}

# Content-type -> preferred mood mapping.
_CONTENT_TYPE_MOODS: dict[str, list[str]] = {
    "podcast": ["chill", "focus"],
    "interview": ["chill", "focus"],
    "tutorial": ["focus", "chill"],
    "lecture": ["focus", "chill"],
    "commentary": ["dark", "epic", "action"],
    "debate": ["dark", "epic", "action"],
    "vlog": ["happy", "chill", "comedy"],
    "other": ["chill", "happy", "epic"],
}


@dataclass
class MusicTrack:
    path: str
    filename: str
    mood: str
    duration_seconds: float = 0.0


def _detect_mood_from_filename(filename: str) -> str:
    """Auto-detect mood from filename keywords."""
    name_lower = filename.lower().replace("-", " ").replace("_", " ")
    for mood, keywords in _MOOD_KEYWORDS.items():
        for kw in keywords:
            if kw in name_lower:
                return mood
    return "chill"  # default mood


def _get_music_dir() -> str:
    """Return the path to the music/ directory."""
    return os.path.join(os.path.dirname(os.path.dirname(__file__)), "music")


def scan_music_library() -> list[MusicTrack]:
    """Scan the music/ directory and return available tracks with detected moods."""
    music_dir = _get_music_dir()
    if not os.path.isdir(music_dir):
        return []

    tracks: list[MusicTrack] = []
    for fname in os.listdir(music_dir):
        if not fname.lower().endswith((".mp3", ".wav", ".ogg", ".m4a")):
            continue
        fpath = os.path.join(music_dir, fname)
        if not os.path.isfile(fpath):
            continue

        mood = _detect_mood_from_filename(fname)
        tracks.append(MusicTrack(
            path=fpath,
            filename=fname,
            mood=mood,
        ))

    return tracks


def select_music(
    content_type: str = "other",
    mood_override: str = "",
    exclude_paths: Optional[list[str]] = None,
) -> Optional[MusicTrack]:
    """Select a background music track based on content type and mood.

    Priority:
      1. Mood override if specified
      2. Content-type-based mood selection
      3. Random selection from matching mood tracks
      4. Random selection from all tracks if no mood match

    Returns None if no music files are available.
    """
    tracks = scan_music_library()
    if not tracks:
        return None

    exclude = set(exclude_paths or [])

    # Determine target mood.
    if mood_override:
        target_mood = mood_override.lower().strip()
    else:
        preferred_moods = _CONTENT_TYPE_MOODS.get(content_type, ["chill"])
        target_mood = random.choice(preferred_moods)

    # Filter to matching mood, excluding already-used tracks.
    matching = [t for t in tracks if t.mood == target_mood and t.path not in exclude]

    # Fallback: try any track in the preferred mood list.
    if not matching:
        for mood in _CONTENT_TYPE_MOODS.get(content_type, []):
            matching = [t for t in tracks if t.mood == mood and t.path not in exclude]
            if matching:
                break

    # Final fallback: any track not yet used.
    if not matching:
        matching = [t for t in tracks if t.path not in exclude]

    # Last resort: any track at all.
    if not matching:
        matching = tracks

    return random.choice(matching)


def get_available_moods() -> list[str]:
    """Return list of moods that have at least one track in the library."""
    tracks = scan_music_library()
    return sorted(set(t.mood for t in tracks))


def get_music_stats() -> dict[str, int]:
    """Return {mood: count} for the music library."""
    tracks = scan_music_library()
    stats: dict[str, int] = {}
    for t in tracks:
        stats[t.mood] = stats.get(t.mood, 0) + 1
    return stats
