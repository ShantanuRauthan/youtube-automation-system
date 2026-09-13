"""Download source videos — yt-dlp primary, pytubefix fallback."""

from __future__ import annotations

import os

import yt_dlp


def _download_ytdlp(video_id: str, dest_dir: str) -> str | None:
    """Try downloading with yt-dlp. Returns path or None on failure."""
    out_template = os.path.join(dest_dir, f"{video_id}.%(ext)s")

    ydl_opts = {
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "outtmpl": out_template,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "socket_timeout": 20,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={video_id}"])
    except Exception:
        return None

    for ext in ("mp4", "mkv", "webm"):
        candidate = os.path.join(dest_dir, f"{video_id}.{ext}")
        if os.path.exists(candidate):
            return candidate
    return None


def _download_pytubefix(video_id: str, dest_dir: str) -> str | None:
    """Fallback: download with pytubefix. Returns path or None on failure."""
    try:
        from pytubefix import YouTube
    except ImportError:
        return None

    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        yt = YouTube(url, use_po_token=False)
        stream = yt.streams.filter(progressive=True, file_extension="mp4").order_by("resolution").desc().first()
        if not stream:
            stream = yt.streams.filter(file_extension="mp4").first()
        if not stream:
            return None

        out_path = stream.download(output_path=dest_dir, filename=f"{video_id}.mp4")
        if os.path.exists(out_path):
            return out_path
    except Exception:
        return None
    return None


def download_video(video_id: str, dest_dir: str) -> str:
    """Download a video as mp4 and return the local file path.

    Tries yt-dlp first, falls back to pytubefix if yt-dlp fails.
    """
    os.makedirs(dest_dir, exist_ok=True)

    # Try yt-dlp first.
    path = _download_ytdlp(video_id, dest_dir)
    if path:
        return path

    # Fallback to pytubefix.
    path = _download_pytubefix(video_id, dest_dir)
    if path:
        return path

    raise FileNotFoundError(f"Download failed for {video_id} (both yt-dlp and pytubefix)")
