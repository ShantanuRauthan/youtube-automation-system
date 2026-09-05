"""Search YouTube for high-view videos on a topic via the Data API v3."""

from __future__ import annotations

import re
from dataclasses import dataclass

from googleapiclient.discovery import build

from config import config


@dataclass
class VideoInfo:
    video_id: str
    title: str
    channel: str
    views: int
    duration_seconds: int

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


def _parse_iso8601_duration(duration: str) -> int:
    """Convert an ISO-8601 duration (e.g. 'PT1H2M10S') to seconds."""
    match = re.match(
        r"P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration or ""
    )
    if not match:
        return 0
    days, hours, minutes, seconds = (int(g) if g else 0 for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


# YouTube's Music category id. Sorting any broad query by raw view count floats
# music videos to the top (they dominate global view counts), which is why a
# "motivational speech" search was returning songs literally titled "Motivation".
# We drop this category unless the user explicitly wants music.
_MUSIC_CATEGORY_ID = "10"


def search_videos(query: str) -> list[VideoInfo]:
    """Return topically-relevant, high-view videos for a query.

    Strategy: search by RELEVANCE (not raw viewCount, which is music-biased),
    pull a large candidate pool, drop Music-category results, then sort the
    remaining relevant videos by view count.
    """
    youtube = build("youtube", "v3", developerKey=config.youtube_api_key, cache_discovery=False)

    # Pull a big relevant candidate pool so filtering still leaves enough.
    search_resp = (
        youtube.search()
        .list(
            q=query,
            part="id",
            type="video",
            order="relevance",
            maxResults=50,
            relevanceLanguage="en",
            videoEmbeddable="true",
        )
        .execute()
    )

    ids = [item["id"]["videoId"] for item in search_resp.get("items", []) if item["id"].get("videoId")]
    if not ids:
        return []

    details = (
        youtube.videos()
        .list(part="statistics,contentDetails,snippet", id=",".join(ids))
        .execute()
    )

    results: list[VideoInfo] = []
    for item in details.get("items", []):
        snippet = item.get("snippet", {})
        views = int(item.get("statistics", {}).get("viewCount", 0))
        duration = _parse_iso8601_duration(item.get("contentDetails", {}).get("duration", ""))

        # Skip music videos (unless the user explicitly allows them) — this is
        # the main reason songs were showing up for non-music categories.
        if config.exclude_music and snippet.get("categoryId") == _MUSIC_CATEGORY_ID:
            continue
        if views < config.min_views:
            continue
        if duration == 0 or duration > config.max_source_seconds:
            continue

        results.append(
            VideoInfo(
                video_id=item["id"],
                title=snippet.get("title", ""),
                channel=snippet.get("channelTitle", ""),
                views=views,
                duration_seconds=duration,
            )
        )

    # Among the topically-relevant results, prefer the most-viewed.
    results.sort(key=lambda v: v.views, reverse=True)
    return results[: config.max_videos]