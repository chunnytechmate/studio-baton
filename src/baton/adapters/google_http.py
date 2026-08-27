"""One place that assembles the HTTP transport every Google API call uses.

Two things live here, both consequences of building that transport ourselves
instead of letting ``googleapiclient`` build it: the deadline it would not have
given us, and the 308 handling it would have given us for free.

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

The cost of taking the transport over is that ``googleapiclient.http.build_http``
no longer runs, and one of the things it did was teach httplib2 not to treat a
resumable upload's ``308 Resume Incomplete`` as a redirect. :func:`build_kwargs`
has to do that itself now; the comment there says why.
"""

from __future__ import annotations

import contextlib
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
        that cannot be assembled, ``{"credentials": credentials}``. A returned
        transport also excludes 308 from the statuses it follows as redirects,
        which resumable uploads depend on.

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
    # A resumable upload answers every accepted chunk with `308 Resume
    # Incomplete`, carrying a `Range:` header and no `Location:`. httplib2
    # counts 308 among its redirect codes, so it reads that as a broken
    # redirect and raises `RedirectMissingLocation` before the API client ever
    # sees the status it was waiting for — every YouTube upload past the first
    # chunk dies there. `googleapiclient.http.build_http` drops 308 for exactly
    # this reason; assembling the transport here means dropping it here too.
    with contextlib.suppress(AttributeError):  # httplib2 < 0.15 has no such set
        transport.redirect_codes = transport.redirect_codes - {308}
    return {"http": google_auth_httplib2.AuthorizedHttp(credentials, http=transport)}


__all__ = ["DEFAULT_TIMEOUT_SECONDS", "build_kwargs"]
