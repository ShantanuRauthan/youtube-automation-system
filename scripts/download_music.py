#!/usr/bin/env python3
"""Download royalty-free background music from Pixabay.

    python scripts/download_music.py

Prompts for a Pixabay API key (free at https://pixabay.com/api/docs/),
then downloads 3-5 tracks per mood into the music/ directory. All tracks
are CC0 licensed (no attribution required).

Tracks are named with mood keywords so the auto-detection in pipeline/music.py
works out of the box.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
import json
from pathlib import Path

# Mood -> search keywords and how many tracks to download.
MOODS: dict[str, dict] = {
    "chill": {
        "keywords": ["lofi chill", "ambient calm", "relax smooth"],
        "count": 5,
    },
    "happy": {
        "keywords": ["upbeat happy", "positive fun", "bright pop"],
        "count": 5,
    },
    "dark": {
        "keywords": ["dark suspense", "mystery tense", "noir eerie"],
        "count": 5,
    },
    "epic": {
        "keywords": ["epic cinematic", "dramatic trailer", "orchestral powerful"],
        "count": 5,
    },
    "sad": {
        "keywords": ["melancholy emotional", "piano gentle", "reflective somber"],
        "count": 3,
    },
    "focus": {
        "keywords": ["minimal study", "corporate clean", "tech concentration"],
        "count": 3,
    },
    "comedy": {
        "keywords": ["quirky playful", "funny lighthearted", "goofy silly"],
        "count": 3,
    },
    "action": {
        "keywords": ["energetic fast", "workout sports", "adrenaline pump"],
        "count": 3,
    },
}

MUSIC_DIR = Path(__file__).parent.parent / "music"
API_BASE = "https://pixabay.com/api/"
MIN_DURATION = 30   # seconds — skip very short jingles
MAX_DURATION = 180  # seconds — skip very long tracks


def get_api_key() -> str:
    """Prompt for or load the Pixabay API key."""
    key = os.getenv("PIXABAY_API_KEY", "").strip()
    if key:
        print(f"Using PIXABAY_API_KEY from environment: {key[:8]}...")
        return key

    print("\nPixabay Music Downloader")
    print("=" * 50)
    print("Get a FREE API key at: https://pixabay.com/api/docs/")
    print("(Sign up → API tab → copy your key)")
    print()
    key = input("Enter your Pixabay API key: ").strip()
    if not key:
        print("Error: API key is required.")
        sys.exit(1)
    return key


def search_music(api_key: str, query: str, per_page: int = 20) -> list[dict]:
    """Search Pixabay for music tracks matching a query."""
    params = urllib.parse.urlencode({
        "key": api_key,
        "q": query,
        "media_type": "music",
        "per_page": per_page,
        "safesearch": "true",
    })
    url = f"{API_BASE}?{params}"

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "YouTubeShortsAutomation/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data.get("hits", [])
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(f"  Rate limited, waiting 60s...")
            time.sleep(60)
            return search_music(api_key, query, per_page)
        print(f"  API error {e.code}: {e.reason}")
        return []
    except Exception as e:
        print(f"  Request failed: {e}")
        return []


def download_track(url: str, dest: Path) -> bool:
    """Download a single MP3 file."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "YouTubeShortsAutomation/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            dest.write_bytes(resp.read())
            return True
    except Exception as e:
        print(f"  Download failed: {e}")
        return False


def sanitize_filename(name: str) -> str:
    """Make a string safe for use as a filename."""
    return "".join(c if c.isalnum() or c in "-_ " else "" for c in name).strip()


def download_for_mood(api_key: str, mood: str, config: dict) -> list[str]:
    """Download tracks for a specific mood. Returns list of downloaded filenames."""
    keywords = config["keywords"]
    target_count = config["count"]
    downloaded: list[str] = []

    for keyword in keywords:
        if len(downloaded) >= target_count:
            break

        print(f"\n  Searching: '{keyword}' ...")
        tracks = search_music(api_key, keyword, per_page=10)

        # Filter by duration.
        valid = [
            t for t in tracks
            if MIN_DURATION <= t.get("duration", 0) <= MAX_DURATION
        ]

        if not valid:
            print(f"  No tracks in duration range ({MIN_DURATION}-{MAX_DURATION}s)")
            continue

        for track in valid:
            if len(downloaded) >= target_count:
                break

            audio_url = track.get("audio", "")
            if not audio_url:
                continue

            # Build filename with mood keyword.
            tags = track.get("tags", "")
            artist = track.get("user", "unknown")
            name_part = sanitize_filename(tags[:40]) if tags else sanitize_filename(keyword)
            filename = f"{mood}-{name_part}.mp3"
            dest = MUSIC_DIR / filename

            if dest.exists():
                print(f"  Already exists: {filename}")
                downloaded.append(filename)
                continue

            print(f"  Downloading: {filename}")
            if download_track(audio_url, dest):
                downloaded.append(filename)
                # Be nice to the API.
                time.sleep(1)

    return downloaded


def main() -> int:
    api_key = get_api_key()
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)

    print(f"\nDownloading royalty-free music to: {MUSIC_DIR}")
    print(f"Target: {sum(m['count'] for m in MOODS.values())} tracks across {len(MOODS)} moods\n")

    total = 0
    for mood, config in MOODS.items():
        print(f"\n{'='*50}")
        print(f"  MOOD: {mood.upper()} (target: {config['count']} tracks)")
        print(f"{'='*50}")

        files = download_for_mood(api_key, mood, config)
        total += len(files)
        print(f"  -> Downloaded {len(files)} track(s) for {mood}")

    print(f"\n{'='*50}")
    print(f"  DONE! Downloaded {total} tracks total.")
    print(f"  Music directory: {MUSIC_DIR}")
    print(f"\n  To enable: set MUSIC_MODE=auto in .env")
    print(f"{'='*50}\n")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
