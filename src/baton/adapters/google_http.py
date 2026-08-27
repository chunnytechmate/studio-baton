"""One place that decides how long a Google API call may hang.

``googleapiclient`` builds its transport from ``httplib2.Http()``, whose
timeout defaults to the process-wide socket default — which is ``None``. A
Drive listing, a Calendar insert, or a YouTube upload chunk that never gets an
answer therefore never returns either, and no retry policy above it ever runs
because nothing above it is reached.

Every other remote call Baton makes is already bounded: `core.retry.http_request`
forces a timeout onto `requests`, and the encoder kills ffmpeg on its own
deadline. This module closes the last hole. It matters most under an agent
harness, where the visible symptom of an unbounded call is a shell command that
hangs until the harness kills it — leaving a booking that may or may not have
been made, with nothing written down either way.
"""

from __future__ import annotations

from typing import Any

#: Long enough for one resumable upload chunk on a domestic uplink, short
#: enough that a wedged connection is noticed the same hour. Calendar overrides
#: this downward: nobody books a lesson interactively for five minutes.
DEFAULT_TIMEOUT_SECONDS = 300.0


def build_kwargs(credentials: Any, timeout: float | None) -> dict[str, Any]:
    """Arguments for ``build()`` that carry ``credentials`` under a deadline.

    Args:
        credentials: Google credentials for the service.
        timeout: Seconds any single HTTP exchange may take. ``None`` or a
            non-positive value keeps the library default (no deadline).

    Returns:
        Either ``{"http": <authorized transport with a timeout>}`` or, when
        that cannot be assembled, ``{"credentials": credentials}``.

    ``build()`` rejects being given both ``credentials`` and ``http``, so the
    timeout has to be applied by wrapping the credentials in the transport
    rather than beside them.

    Falling back rather than raising is deliberate: ``google-auth-httplib2``
    ships as a dependency of ``google-api-python-client``, so its absence means
    an installation odd enough that refusing to work at all would be a worse
    answer than working without a deadline.
    """
    if not timeout or timeout <= 0:
        return {"credentials": credentials}
    try:
        import google_auth_httplib2
        import httplib2
    except ImportError:  # pragma: no cover - only on a hand-assembled install
        return {"credentials": credentials}

    transport = httplib2.Http(timeout=timeout)
    return {"http": google_auth_httplib2.AuthorizedHttp(credentials, http=transport)}


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "build_kwargs"]
