"""Download source videos with yt-dlp."""

from __future__ import annotations

import os

import yt_dlp


def download_video(video_id: str, dest_dir: str) -> str:
    """Download a video as mp4 and return the local file path."""
    os.makedirs(dest_dir, exist_ok=True)
    out_template = os.path.join(dest_dir, f"{video_id}.%(ext)s")

    ydl_opts = {
        # Prefer an mp4 <=1080p so FFmpeg has clean H.264 to work with.
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={video_id}"])

    # yt-dlp may output .mp4 (or occasionally .mkv when merge falls back).
    for ext in ("mp4", "mkv", "webm"):
        candidate = os.path.join(dest_dir, f"{video_id}.{ext}")
        if os.path.exists(candidate):
            return candidate

    raise FileNotFoundError(f"Download finished but no file found for {video_id}")
