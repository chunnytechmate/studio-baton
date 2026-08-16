"""Google Drive as a source, YouTube as a publisher.

Both live behind the ``google`` extra. Importing the SDK lazily means the core
install stays small and someone using Baton without video never installs it —
and if they turn video on without it, they get a sentence telling them what to
install rather than an ImportError traceback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from ...core.config import Config
from ...errors import ConfigError, UpstreamError
from .base import SourceClip, UploadResult


def _require_google() -> Any:
    """Import the Google client libraries, or explain how to get them."""
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise ConfigError(
            "The Google client libraries are not installed.",
            remedy="Install them with `pip install 'studio-baton[google]'`.",
        ) from exc
    return build, MediaFileUpload, Credentials


def _credentials(config: Config, scopes: list[str]) -> Any:
    """Build OAuth credentials from a stored refresh token."""
    _, _, Credentials = _require_google()
    return Credentials(
        token=None,
        refresh_token=str(config.secret("media.youtube.refresh_token_env")),
        client_id=str(config.secret("media.youtube.client_id_env")),
        client_secret=str(config.secret("media.youtube.client_secret_env")),
        token_uri="https://oauth2.googleapis.com/token",  # noqa: S106 - a public endpoint
        scopes=scopes,
    )


class DriveSource:
    """Clips waiting in per-learner subfolders of one Drive folder."""

    driver = "gdrive"

    #: Read plus the ability to trash a file we collected.
    SCOPES: ClassVar[list[str]] = ["https://www.googleapis.com/auth/drive"]

    def __init__(self, folder_id: str, config: Config, *, download_retries: int = 3) -> None:
        self.folder_id = folder_id
        self.config = config
        self.download_retries = download_retries
        self._service: Any = None

    @classmethod
    def from_config(cls, config: Config) -> DriveSource:
        return cls(
            folder_id=str(config.secret("media.drive.folder_id_env")),
            config=config,
            download_retries=int(config.get("media.drive.download_retries", 3)),
        )

    @property
    def service(self) -> Any:
        if self._service is None:
            build, _, _ = _require_google()
            self._service = build(
                "drive",
                "v3",
                credentials=_credentials(self.config, self.SCOPES),
                cache_discovery=False,
            )
        return self._service

    def _children(self, parent_id: str, *, folders: bool) -> list[dict[str, Any]]:
        mime = "=" if folders else "!="
        query = (
            f"'{parent_id}' in parents and trashed = false "
            f"and mimeType {mime} 'application/vnd.google-apps.folder'"
        )
        found: list[dict[str, Any]] = []
        page_token = None
        while True:
            response = (
                self.service.files()
                .list(
                    q=query,
                    fields="nextPageToken, files(id, name, size, createdTime)",
                    pageToken=page_token,
                    pageSize=200,
                )
                .execute()
            )
            found.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return found

    def list_pending(self) -> list[SourceClip]:
        clips: list[SourceClip] = []
        for folder in self._children(self.folder_id, folders=True):
            for item in self._children(folder["id"], folders=False):
                clips.append(
                    SourceClip(
                        id=item["id"],
                        name=item.get("name", ""),
                        learner_folder=folder.get("name", ""),
                        size_bytes=int(item.get("size", 0) or 0),
                        created_at=str(item.get("createdTime", "")),
                    )
                )
        return clips

    def download(self, clip: SourceClip, destination: Path) -> Path:
        from googleapiclient.http import MediaIoBaseDownload

        destination.parent.mkdir(parents=True, exist_ok=True)
        request = self.service.files().get_media(fileId=clip.id)
        with open(destination, "wb") as handle:
            downloader = MediaIoBaseDownload(handle, request, chunksize=8 * 1024 * 1024)
            done = False
            while not done:
                _, done = downloader.next_chunk(num_retries=self.download_retries)

        # A truncated download is the failure that produces a video with the
        # last minute of the lesson missing, uploaded and linked before anyone
        # watches it. Size is the cheap check that catches it.
        if clip.size_bytes and destination.stat().st_size != clip.size_bytes:
            actual = destination.stat().st_size
            destination.unlink(missing_ok=True)
            raise UpstreamError(
                f"{clip.name} downloaded {actual} bytes, expected {clip.size_bytes}.",
                service="gdrive",
                remedy="Re-run; the partial file has been removed.",
            )
        return destination

    def trash(self, clip_ids: list[str]) -> int:
        moved = 0
        for clip_id in clip_ids:
            self.service.files().update(fileId=clip_id, body={"trashed": True}).execute()
            moved += 1
        return moved

    def health(self) -> None:
        self.service.files().get(fileId=self.folder_id, fields="id").execute()


class YouTubePublisher:
    """Uploads the finished file to YouTube."""

    driver = "youtube"

    SCOPES: ClassVar[list[str]] = ["https://www.googleapis.com/auth/youtube.upload"]

    def __init__(self, config: Config, *, category_id: str = "22") -> None:
        self.config = config
        self.category_id = category_id
        self._service: Any = None

    @classmethod
    def from_config(cls, config: Config) -> YouTubePublisher:
        return cls(config, category_id=str(config.get("media.youtube.category_id", "22")))

    @property
    def service(self) -> Any:
        if self._service is None:
            build, _, _ = _require_google()
            self._service = build(
                "youtube",
                "v3",
                credentials=_credentials(self.config, self.SCOPES),
                cache_discovery=False,
            )
        return self._service

    def upload(
        self, path: Path, *, title: str, description: str = "", privacy: str = "unlisted"
    ) -> UploadResult:
        _, MediaFileUpload, _ = _require_google()
        if not path.is_file():
            raise ConfigError(f"No file to upload at {path}.")

        media = MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True)
        request = self.service.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": title[:100],  # YouTube's hard limit
                    "description": description,
                    "categoryId": self.category_id,
                },
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
            },
            media_body=media,
        )

        response = None
        while response is None:
            _, response = request.next_chunk(num_retries=3)

        video_id = str(response.get("id", ""))
        if not video_id:
            raise UpstreamError(
                "YouTube accepted the upload but returned no id.", service="youtube"
            )
        return UploadResult(
            video_id=video_id,
            url=f"https://youtu.be/{video_id}",
            title=title,
        )

    def health(self) -> None:
        self.service.channels().list(part="id", mine=True).execute()
