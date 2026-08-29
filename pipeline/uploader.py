"""Upload finished Shorts to YouTube using Google OAuth (installed-app flow).

The first run opens a browser to authorize; the refresh token is then cached in
TOKEN_FILE so subsequent runs are fully non-interactive.
"""

from __future__ import annotations

import os

from config import config

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _get_authenticated_service():
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

    return build("youtube", "v3", credentials=creds, cache_discovery=False)


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
