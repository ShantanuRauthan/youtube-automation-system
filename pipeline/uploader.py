"""Upload finished Shorts to YouTube using Google OAuth (installed-app flow).

The first run opens a browser to authorize; the refresh token is then cached in
TOKEN_FILE so subsequent runs are fully non-interactive.

Scopes:
  * youtube.upload   — publish Shorts and set custom thumbnails.
  * youtube.readonly — read view/like/comment stats for the analytics loop.

NOTE: if you authorized previously with only the upload scope, delete TOKEN_FILE
(default token.json) once and re-run so the new readonly scope is granted.
"""

from __future__ import annotations

import os

from config import config

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

# Reuse one authenticated client across calls in a single process.
_service = None


def _get_authenticated_service():
    global _service
    if _service is not None:
        return _service

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(config.token_file):
        creds = Credentials.from_authorized_user_file(config.token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(config.client_secret_file):
                raise FileNotFoundError(
                    f"OAuth client file '{config.client_secret_file}' not found. "
                    "Create a Desktop OAuth client in Google Cloud Console and download it."
                )
            flow = InstalledAppFlow.from_client_secrets_file(config.client_secret_file, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(config.token_file, "w", encoding="utf-8") as token:
            token.write(creds.to_json())

    _service = build("youtube", "v3", credentials=creds, cache_discovery=False)
    return _service


def upload_short(
    file_path: str,
    title: str,
    description: str,
    tags: list[str],
    category_id: str,
) -> str:
    """Upload a Short and return its YouTube video ID."""
    from googleapiclient.http import MediaFileUpload

    youtube = _get_authenticated_service()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": config.upload_privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(file_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _status, response = request.next_chunk()

    return response["id"]


def set_thumbnail(video_id: str, thumb_path: str) -> None:
    """Set a custom thumbnail on an uploaded video.

    Requires a channel that is enabled for custom thumbnails (phone-verified).
    Raises on failure so the caller can warn and continue.
    """
    from googleapiclient.http import MediaFileUpload

    if not os.path.exists(thumb_path):
        raise FileNotFoundError(thumb_path)

    youtube = _get_authenticated_service()
    youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(thumb_path, mimetype="image/jpeg"),
    ).execute()


def fetch_stats(video_ids: list[str]) -> dict[str, dict]:
    """Return {video_id: {viewCount, likeCount, commentCount, ...}} for owned videos."""
    if not video_ids:
        return {}

    youtube = _get_authenticated_service()
    out: dict[str, dict] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        resp = youtube.videos().list(part="statistics", id=",".join(chunk)).execute()
        for item in resp.get("items", []):
            out[item["id"]] = item.get("statistics", {})
    return out
