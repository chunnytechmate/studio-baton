"""Google Drive as a source, YouTube as a publisher.

Both live behind the ``google`` extra. Importing the SDK lazily means the core
install stays small and someone using Baton without video never installs it —
and if they turn video on without it, they get a sentence telling them what to
install rather than an ImportError traceback.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from ...core.config import Config
from ...errors import ConfigError, UpstreamError
from .base import VIDEO_SUFFIXES, SourceClip, UploadResult


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


_T = TypeVar("_T")


def _credentials(config: Config, section: str) -> Any:
    """Build credentials without changing the refresh token's original scopes."""
    _, _, Credentials = _require_google()
    credentials_file = str(config.get(f"{section}.credentials_file", "")).strip()
    if credentials_file:
        path = config.path(f"{section}.credentials_file")
        if not path.is_file():
            raise ConfigError(
                f"No Google credentials file exists at {path}.",
                remedy=f"Correct `{section}.credentials_file`, or remove it and configure "
                "the service's refresh-token environment variables.",
            )
        try:
            # An authorized-user refresh token is bound to the scopes granted
            # when it was issued. Replacing them here can make Google reject a
            # valid token with `invalid_scope` before the API call is attempted.
            return Credentials.from_authorized_user_file(str(path))
        except (OSError, ValueError) as exc:
            raise ConfigError(
                f"The Google credentials file at {path} cannot be read.",
                remedy="Replace it with a valid authorized-user credentials JSON file.",
            ) from exc

    return Credentials(
        token=None,
        refresh_token=str(config.secret(f"{section}.refresh_token_env")),
        client_id=str(config.secret(f"{section}.client_id_env")),
        client_secret=str(config.secret(f"{section}.client_secret_env")),
        token_uri="https://oauth2.googleapis.com/token",  # noqa: S106 - a public endpoint
    )


def _google_call(service: str, operation: Callable[[], _T]) -> _T:
    """Keep vendor exceptions inside Baton's exit/JSON contract."""
    try:
        return operation()
    except (ConfigError, UpstreamError):
        raise
    except Exception as exc:
        raise UpstreamError(
            f"{service} request failed: {type(exc).__name__}.",
            service=service,
            remedy="Check the service credentials and permissions, then re-run.",
        ) from exc


class DriveSource:
    """Clips waiting in per-learner subfolders of one Drive folder."""

    driver = "gdrive"

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
            self._service = _google_call(
                "gdrive",
                lambda: build(
                    "drive",
                    "v3",
                    credentials=_credentials(self.config, "media.drive"),
                    cache_discovery=False,
                ),
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
            request = self.service.files().list(
                q=query,
                fields="nextPageToken, files(id, name, size, createdTime)",
                pageToken=page_token,
                pageSize=200,
            )
            response = _google_call("gdrive", request.execute)
            found.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return found

    def list_pending(self) -> list[SourceClip]:
        clips: list[SourceClip] = []
        for folder in self._children(self.folder_id, folders=True):
            for item in self._children(folder["id"], folders=False):
                name = str(item.get("name", ""))
                # The local source has always filtered by extension; a folder
                # with a photo or a note in it is normal, and a note is not
                # a clip no matter which folder it is in.
                if Path(name).suffix.lower() not in VIDEO_SUFFIXES:
                    continue
                clips.append(
                    SourceClip(
                        id=item["id"],
                        name=name,
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
                _, done = _google_call(
                    "gdrive", lambda: downloader.next_chunk(num_retries=self.download_retries)
                )

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
            request = self.service.files().update(fileId=clip_id, body={"trashed": True})
            _google_call("gdrive", request.execute)
            moved += 1
        return moved

    def health(self) -> None:
        request = self.service.files().get(fileId=self.folder_id, fields="id")
        _google_call("gdrive", request.execute)


class YouTubePublisher:
    """Uploads the finished file to YouTube."""

    driver = "youtube"

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
            self._service = _google_call(
                "youtube",
                lambda: build(
                    "youtube",
                    "v3",
                    credentials=_credentials(self.config, "media.youtube"),
                    cache_discovery=False,
                ),
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
            _, response = _google_call("youtube", lambda: request.next_chunk(num_retries=3))

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
        request = self.service.channels().list(part="id", mine=True)
        _google_call("youtube", request.execute)
