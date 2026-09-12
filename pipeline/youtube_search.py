"""Search YouTube for high-view videos on a topic via the Data API v3.

Sourcing modes (config.source_mode / SOURCE_MODE):
  * relevance — topical match, then sort by total views (default, safest).
  * views     — same as relevance (kept as an explicit alias).
  * trending  — recent uploads, then sort by view VELOCITY (views per hour
                since publish), which surfaces fast-rising videos early.

In every mode we search topically first, drop Music-category results, then
rank — so sorting by views can't hijack a category with unrelated songs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from googleapiclient.discovery import build

from config import config


@dataclass
class VideoInfo:
    video_id: str
    title: str
    channel: str
    views: int
    duration_seconds: int
    channel_id: str = ""
    published_at: str = ""
    views_per_hour: float = 0.0

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


def _hours_since(published_at: str) -> float:
    """Hours elapsed since an ISO-8601 publish timestamp (min 1.0)."""
    if not published_at:
        return 1.0
    try:
        dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return max(1.0, delta.total_seconds() / 3600.0)
    except ValueError:
        return 1.0


# YouTube's Music category id. Sorting any broad query by raw view count floats
# music videos to the top (they dominate global view counts), which is why a
# "motivational speech" search was returning songs literally titled "Motivation".
# We drop this category unless the user explicitly wants music.
_MUSIC_CATEGORY_ID = "10"


def search_videos(query: str, mode: str | None = None) -> list[VideoInfo]:
    """Return topically-relevant, high-view videos for a query.

    Strategy: search topically (NOT by raw viewCount, which is music-biased),
    pull a large candidate pool, drop Music-category results, then rank by
    total views (relevance/views) or view velocity (trending).
    """
    mode = (mode or config.source_mode or "relevance").strip().lower()

    youtube = build("youtube", "v3", developerKey=config.youtube_api_key, cache_discovery=False)

    pool = max(10, min(config.search_pool, 50))
    # "trending" wants fresh uploads (sort by date, then rank by velocity below);
    # everything else wants the most topically relevant candidates.
    order = "date" if mode == "trending" else "relevance"

    search_resp = (
        youtube.search()
        .list(
            q=query,
            part="id",
            type="video",
            order=order,
            maxResults=pool,
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

        # Skip music videos (unless explicitly allowed) — the main reason songs
        # were showing up for non-music categories.
        if config.exclude_music and snippet.get("categoryId") == _MUSIC_CATEGORY_ID:
            continue
        if views < config.min_views:
            continue
        if duration == 0 or duration > config.max_source_seconds:
            continue
        # Skip very short sources (e.g. other people's Shorts): clipping a 30-60s
        # Short out of a 17s video is a near-1:1 repost. A longer source means the
        # Short is genuinely a small excerpt. Floor disabled when set to 0.
        if config.min_source_seconds and duration < config.min_source_seconds:
            continue

        published = snippet.get("publishedAt", "")
        results.append(
            VideoInfo(
                video_id=item["id"],
                title=snippet.get("title", ""),
                channel=snippet.get("channelTitle", ""),
                views=views,
                duration_seconds=duration,
                channel_id=snippet.get("channelId", ""),
                published_at=published,
                views_per_hour=views / _hours_since(published),
            )
        )

    if mode == "trending":
        # Fast-rising: most views accumulated per hour since publishing.
        results.sort(key=lambda v: v.views_per_hour, reverse=True)
    else:
        # Among topically-relevant results, prefer the most-viewed.
        results.sort(key=lambda v: v.views, reverse=True)

    return results[: config.max_videos]


def _resolve_channel_id(youtube, name: str) -> tuple[str, str]:
    """Resolve a person/channel name (or @handle, or channel URL) to a channel id.

    Returns (channel_id, channel_title). Raises ValueError if nothing matches.
    """
    name = name.strip()

    # Accept a raw channel id, a full channel URL, or an @handle.
    m = re.search(r"(UC[0-9A-Za-z_-]{22})", name)
    if m:
        cid = m.group(1)
        resp = youtube.channels().list(part="snippet", id=cid).execute()
        items = resp.get("items", [])
        if items:
            return cid, items[0]["snippet"].get("title", name)

    handle = None
    hm = re.search(r"@([0-9A-Za-z._-]+)", name)
    if hm:
        handle = hm.group(1)
    if handle:
        # forHandle is supported by the Data API and is the most exact match.
        try:
            resp = youtube.channels().list(part="snippet", forHandle=handle).execute()
            items = resp.get("items", [])
            if items:
                return items[0]["id"], items[0]["snippet"].get("title", name)
        except Exception:
            pass

    # Fall back to a channel search by name and take the best match.
    resp = (
        youtube.search()
        .list(q=name, part="snippet", type="channel", maxResults=1)
        .execute()
    )
    items = resp.get("items", [])
    if not items:
        raise ValueError(f"No YouTube channel found for '{name}'.")
    snippet = items[0]["snippet"]
    return items[0]["id"]["channelId"], snippet.get("title", name)


def search_channel_videos(name: str, mode: str | None = None) -> list[VideoInfo]:
    """Pull a person's / channel's own videos and rank them.

    Resolves ``name`` (plain name, @handle, channel id, or channel URL) to a
    channel, lists that channel's uploads, filters by the same view/duration
    rules, then ranks by views (default) or view velocity (trending mode).
    """
    mode = (mode or config.source_mode or "relevance").strip().lower()
    youtube = build("youtube", "v3", developerKey=config.youtube_api_key, cache_discovery=False)

    channel_id, channel_title = _resolve_channel_id(youtube, name)

    pool = max(10, min(config.search_pool, 50))
    order = "date" if mode == "trending" else "viewCount"

    search_resp = (
        youtube.search()
        .list(
            channelId=channel_id,
            part="id",
            type="video",
            order=order,
            maxResults=pool,
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

        # For a specific person we do NOT drop music or enforce MIN_VIEWS — the
        # user explicitly wants THIS channel's content. Only skip clips that are
        # unusable for a Short (no duration / too long to process).
        if duration == 0 or duration > config.max_source_seconds:
            continue

        published = snippet.get("publishedAt", "")
        results.append(
            VideoInfo(
                video_id=item["id"],
                title=snippet.get("title", ""),
                channel=snippet.get("channelTitle", channel_title),
                views=views,
                duration_seconds=duration,
                channel_id=snippet.get("channelId", channel_id),
                published_at=published,
                views_per_hour=views / _hours_since(published),
            )
        )

    if mode == "trending":
        results.sort(key=lambda v: v.views_per_hour, reverse=True)
    else:
        results.sort(key=lambda v: v.views, reverse=True)

    return results[: config.max_videos]
