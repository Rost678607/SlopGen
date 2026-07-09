"""YouTube upload via Data API v3 (resumable).

First run per account opens a browser OAuth consent flow and caches the token.
Quota note: an upload costs 1600 of the 10000 daily units => ~6 uploads/day
per Google Cloud project.
"""

from __future__ import annotations

from pathlib import Path

from ..pipeline.context import AppContext
from ..pipeline.job import VideoJob

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _credentials(client_secret: Path, token_path: Path):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        if not client_secret.exists():
            raise FileNotFoundError(
                f"{client_secret} not found — download the OAuth client JSON "
                "from Google Cloud Console (see README)"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), SCOPES)
        creds = flow.run_local_server(port=0)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    return creds


class YouTubePublisher:
    def publish(self, job: VideoJob, ctx: AppContext) -> str:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        yt_cfg = ctx.account.youtube
        creds = _credentials(yt_cfg.client_secret, yt_cfg.token)
        service = build("youtube", "v3", credentials=creds)

        body = {
            "snippet": {
                "title": job.metadata["title"],
                "description": job.metadata["description"],
                "tags": job.metadata["tags"],
                "categoryId": yt_cfg.category_id,
            },
            "status": {"privacyStatus": yt_cfg.privacy, "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(str(job.final_path), mimetype="video/mp4", resumable=True)
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _, response = request.next_chunk()
        return f"https://youtube.com/shorts/{response['id']}"
