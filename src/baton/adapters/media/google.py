"""Google Drive as a source, YouTube as a publisher.

Both live behind the ``google`` extra. Importing the SDK lazily means the core
install stays small and someone using Baton without video never installs it,
and if they turn video on without it, they get a sentence telling them what to
install rather than an ImportError traceback.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from ...core.config import Config
from ...errors import ConfigError, UpstreamError
from .. import google_http
from .base import GONE, TRASHED, UNFILED, VIDEO_SUFFIXES, SourceClip, TrashOutcome, UploadResult

_VIDEO_ID = re.compile(r"(?:youtu\.be/|[?&]v=|/embed/)(?P<id>[A-Za-z0-9_-]{11})")


def extract_video_id(url: str) -> str | None:
    """The 11-character video id out of any of YouTube's URL shapes.

    Pure string parsing, no API call, no `[google]` extra required, so a
    caller can use this to decide whether a Notion link is even a YouTube
    link before paying for credentials or a network round trip.
    """
    if not url:
        return None
    match = _VIDEO_ID.search(url)
    return match.group("id") if match else None


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


def _timeout(config: Config, section: str) -> float:
    """Seconds one HTTP exchange with this service may take.

    Per-section rather than global: a YouTube upload chunk and a Drive metadata
    listing do not deserve the same patience, and a studio on a slow uplink
    needs to be able to raise one without raising the other.
    """
    return float(config.get(f"{section}.timeout_seconds", google_http.DEFAULT_TIMEOUT_SECONDS))


def _credentials(config: Config, section: str, *, override_file: str | None = None) -> Any:
    """Build credentials without changing the refresh token's original scopes.

    ``override_file`` names an authorized-user JSON that takes the place of
    ``<section>.credentials_file``: used by the cleanup credential, which is
    a different account from the one the pipeline reads with.
    """
    _, _, Credentials = _require_google()
    credentials_file = str(override_file or config.get(f"{section}.credentials_file", "")).strip()
    if credentials_file:
        path = Path(credentials_file).expanduser()
        if not path.is_absolute():
            path = config.profile_dir / path
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


def _http_detail(exc: BaseException | None) -> tuple[int | None, str | None]:
    """Status code and reason off a googleapiclient ``HttpError``, if that is
    what this is.

    The class name alone says ``HttpError.`` and nothing else. The status and
    reason were the whole diagnosis, and the production trash failures of
    2026-09-05/06 went undiagnosed for four runs because neither reached the
    job log. Duck-typed rather than an ``isinstance`` on the vendor class so
    the tests can raise a stand-in without the ``[google]`` extra installed.
    """
    if exc is None:
        return None, None
    status = getattr(getattr(exc, "resp", None), "status", None)
    if not isinstance(status, int):
        return None, None
    reason: str | None = None
    try:
        payload = json.loads(getattr(exc, "content", b"") or b"{}")
        error = payload.get("error", {})
        errors = error.get("errors") or [{}]
        reason = errors[0].get("reason") or error.get("message")
    except (AttributeError, TypeError, ValueError):
        reason = None
    return status, str(reason) if reason else None


def _google_call(service: str, operation: Callable[[], _T]) -> _T:
    """Keep vendor exceptions inside Baton's exit/JSON contract."""
    try:
        return operation()
    except (ConfigError, UpstreamError):
        raise
    except Exception as exc:
        status, reason = _http_detail(exc)
        detail = f"{type(exc).__name__}"
        if status:
            detail = f"HttpError {status}"
            if reason:
                detail += f": {reason}"
        raise UpstreamError(
            f"{service} request failed: {detail}.",
            service=service,
            remedy="Check the service credentials and permissions, then re-run.",
        ) from exc


class DriveSource:
    """Clips waiting in per-learner subfolders of one Drive folder."""

    driver = "gdrive"

    def __init__(
        self,
        folder_id: str,
        config: Config,
        *,
        download_retries: int = 3,
        credentials_file: str | None = None,
    ) -> None:
        self.folder_id = folder_id
        self.config = config
        self.download_retries = download_retries
        self.credentials_file = credentials_file
        self._service: Any = None

    @classmethod
    def from_config(cls, config: Config, *, credentials_file: str | None = None) -> DriveSource:
        return cls(
            folder_id=str(config.secret("media.drive.folder_id_env")),
            config=config,
            download_retries=int(config.get("media.drive.download_retries", 3)),
            credentials_file=credentials_file,
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
                    cache_discovery=False,
                    **google_http.build_kwargs(
                        _credentials(
                            self.config, "media.drive", override_file=self.credentials_file
                        ),
                        _timeout(self.config, "media.drive"),
                    ),
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

    def trash(self, clip_ids: list[str]) -> list[TrashOutcome]:
        """Clear the source of clips this pipeline has already published.

        Trashing is the first choice: it is what a studio running everything
        under one account gets. Uploading a file makes the uploader its owner,
        though, and Drive lets only the owner trash a file, so clips arriving
        from a teacher's own account answer every trash request with
        ``403 insufficientFilePermissions`` (in production, every clip of
        2026-09-05/06). Removing the clip from the learner folders reaches
        the state this step owes, nothing left to collect, using rights the
        folder owner already has, and the uploader keeps their file.

        The unfiled clips are returned as ``UNFILED`` outcomes rather than
        counted, because a file that left every folder the credential can see
        is unreachable from then on: only its recorded id can ever find it
        again, and the pipeline owes each one a cleanup-ledger entry.
        """
        subfolders: set[str] | None = None
        results: list[TrashOutcome] = []
        for clip_id in clip_ids:
            request = self.service.files().update(fileId=clip_id, body={"trashed": True})
            try:
                _google_call("gdrive", request.execute)
            except UpstreamError as exc:
                status, reason = _http_detail(exc.__cause__)
                if status == 404:
                    # Out of the source and out of this credential's sight:
                    # nothing left to clear.
                    results.append(TrashOutcome(clip_id, GONE))
                    continue
                if status != 403 or reason != "insufficientFilePermissions":
                    # Rate limits also answer 403; only the owner rule falls
                    # back to removing, everything else still fails the step.
                    raise
                if subfolders is None:
                    subfolders = {f["id"] for f in self._children(self.folder_id, folders=True)}
                seen = self._unfile(clip_id, subfolders)
                results.append(TrashOutcome(clip_id, UNFILED if seen else GONE))
            else:
                results.append(TrashOutcome(clip_id, TRASHED))
        return results

    def _unfile(self, clip_id: str, subfolders: set[str]) -> bool:
        """Take one clip out of the learner folders, leaving the file itself
        with its uploader.

        Returns whether the file was still there to unfile. A clip that
        vanished before this call is gone rather than merely unfiled, and its
        caller owes the ledger nothing.
        """
        try:
            meta = _google_call(
                "gdrive",
                lambda: self.service.files().get(fileId=clip_id, fields="parents").execute(),
            )
        except UpstreamError as exc:
            status, _reason = _http_detail(exc.__cause__)
            if status == 404:
                return False
            raise
        ours = [parent for parent in meta.get("parents", []) if parent in subfolders]
        for parent in ours:
            request = self.service.files().update(fileId=clip_id, removeParents=parent, body={})
            _google_call("gdrive", request.execute)
        return True

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
                    cache_discovery=False,
                    **google_http.build_kwargs(
                        _credentials(self.config, "media.youtube"),
                        _timeout(self.config, "media.youtube"),
                    ),
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

    def _own_channel_ids(self) -> set[str]:
        response = _google_call(
            "youtube", self.service.channels().list(part="id", mine=True).execute
        )
        return {str(item["id"]) for item in response.get("items", [])}

    def owns(self, video_id: str) -> bool:
        """Whether the video belongs to the configured channel.

        Adoption of a link already on a session document rides on this: the
        same ownership rule as ``update_description``, asked as a yes/no so a
        reference video on someone else's channel is simply never adopted
        rather than raising. A video that no longer resolves is not ours
        either.
        """
        listing = _google_call(
            "youtube", self.service.videos().list(part="snippet", id=video_id).execute
        )
        items = listing.get("items", [])
        if not items:
            return False
        our_channel_ids = self._own_channel_ids()
        if not our_channel_ids:
            # The account's channel list came back empty, which says nothing
            # about the video. Not the same as a definite "not ours".
            return False
        return str(items[0]["snippet"].get("channelId", "")) in our_channel_ids

    def update_description(self, video_id: str, description: str) -> None:
        """Replace a video's description, keeping its title, tags, and category.

        Refuses to touch a video the configured account does not own. The
        YouTube link on a session document is read off a Notion page, and
        that field can legitimately hold a *reference* clip: a teacher's
        tutorial on someone else's channel, not the learner's own upload.
        Skipping the ownership check would build the update body from that
        stranger's video and overwrite a third party's description with a
        private lesson summary the moment the account happened to have edit
        access to it (observed against a real reference link, 2026-08-09).
        """
        listing = _google_call(
            "youtube", self.service.videos().list(part="snippet", id=video_id).execute
        )
        items = listing.get("items", [])
        if not items:
            raise UpstreamError(f"No YouTube video found for id {video_id}.", service="youtube")
        snippet = items[0]["snippet"]

        owner_channel_id = str(snippet.get("channelId", ""))
        our_channel_ids = self._own_channel_ids()
        if our_channel_ids and owner_channel_id not in our_channel_ids:
            raise UpstreamError(
                f"Video {video_id} belongs to channel {owner_channel_id} "
                f"('{snippet.get('channelTitle', '?')}'), not to the configured account.",
                service="youtube",
                remedy="Check the YouTube link on the document: it may be a reference "
                "video rather than the learner's own recording. Not updated.",
            )

        body = {
            "id": video_id,
            "snippet": {
                "title": snippet.get("title", ""),
                "description": description,
                "tags": snippet.get("tags", []),
                "categoryId": snippet.get("categoryId", self.category_id),
            },
        }
        request = self.service.videos().update(part="snippet", body=body)
        _google_call("youtube", request.execute)

    def health(self) -> None:
        request = self.service.channels().list(part="id", mine=True)
        _google_call("youtube", request.execute)
