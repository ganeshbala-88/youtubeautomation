"""YouTube upload via the Data API v3 using a long-lived OAuth refresh token."""

from __future__ import annotations

from pathlib import Path

from .config import require_env

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _service():
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    env = require_env("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN")
    credentials = Credentials(
        token=None,
        refresh_token=env["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=env["YT_CLIENT_ID"],
        client_secret=env["YT_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=credentials, cache_discovery=False)


def upload(video: Path, plan, cfg: dict, privacy: str | None = None) -> str:
    """Upload the video and return its watch URL."""
    from googleapiclient.http import MediaFileUpload

    upload_cfg = cfg["upload"]
    body = {
        "snippet": {
            "title": plan.title[:100],
            "description": plan.description[:5000],
            "tags": (plan.tags or upload_cfg["tags"])[:15],
            "categoryId": str(upload_cfg["category_id"]),
        },
        "status": {
            "privacyStatus": privacy or upload_cfg["privacy"],
            "selfDeclaredMadeForKids": bool(upload_cfg["made_for_kids"]),
        },
    }
    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4")
    request = _service().videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[upload] {int(status.progress() * 100)}%")

    video_id = response["id"]
    print(f"[upload] done: https://youtu.be/{video_id}")
    return video_id


def set_thumbnail(video_id: str, thumbnail: Path) -> bool:
    """Custom thumbnails need a verified channel, so failure here is never fatal."""
    from googleapiclient.http import MediaFileUpload

    try:
        _service().thumbnails().set(
            videoId=video_id, media_body=MediaFileUpload(str(thumbnail), mimetype="image/jpeg")
        ).execute()
        print("[upload] thumbnail set")
        return True
    except Exception as exc:  # noqa: BLE001 - thumbnail is best-effort
        print(f"[upload] thumbnail skipped: {exc}")
        return False
