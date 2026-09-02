"""What `adapters.google_http` owes the caller for taking the transport over.

`core.retry.http_request` forces a timeout onto every `requests` call and the
encoder kills ffmpeg on its own clock, but `googleapiclient` builds its
transport from `httplib2.Http()`, whose timeout defaults to the socket default,
which is `None`. A Drive listing that never gets an answer never returned,
and under a harness that reads as a shell command hanging until it is killed,
with a booking that may or may not have been made and nothing written down.

Building that transport ourselves also skips `googleapiclient`'s own setup, so
the 308 handling resumable uploads need is tested here too.
"""

from __future__ import annotations

import pytest

from baton.adapters import google_http


def test_a_timeout_produces_an_authorized_transport():
    # Skipped on a core install: `google-auth-httplib2` arrives with the
    # `google` extra, and CI's test job installs only `dev`. The behaviour when
    # it is missing has its own test below: that path is the one a core
    # install actually takes.
    pytest.importorskip("google_auth_httplib2")

    kwargs = google_http.build_kwargs("fake-credentials", 30.0)

    assert "http" in kwargs
    # `build()` refuses both at once, so the deadline has to arrive *through*
    # the credentials rather than beside them.
    assert "credentials" not in kwargs
    assert kwargs["http"].http.timeout == 30.0


def test_the_transport_does_not_follow_308_as_a_redirect():
    """A resumable upload's ``308 Resume Incomplete`` must reach the API client.

    httplib2 ships 308 among its redirect codes, and a 308 from a resumable
    upload carries `Range:` and no `Location:`, so httplib2 raises
    `RedirectMissingLocation` on the first chunk of any upload big enough to
    need a second one. Every YouTube upload over one chunk (8 MB) failed that
    way in 0.4.0, because taking the transport over here stopped
    `googleapiclient.http.build_http` (which drops 308) from ever running.

    Nothing else in the suite catches this: the video pipeline's tests all run
    against fakes, and no fake speaks httplib2.
    """
    pytest.importorskip("google_auth_httplib2")

    transport = google_http.build_kwargs("fake-credentials", 30.0)["http"].http

    assert 308 not in transport.redirect_codes
    # The other redirects are httplib2's business, not ours.
    assert {301, 302, 303, 307} <= transport.redirect_codes


@pytest.mark.parametrize("timeout", [None, 0, -1])
def test_no_timeout_falls_back_to_plain_credentials(timeout):
    assert google_http.build_kwargs("fake-credentials", timeout) == {
        "credentials": "fake-credentials"
    }


def test_a_missing_helper_library_is_not_fatal(monkeypatch):
    """A core install has no `google-auth-httplib2`, and that must still work.

    It ships with the API client, so on a `[google]` install it is always
    there. Anywhere else, including CI's own test job: the deadline is
    quietly skipped rather than made into a crash.
    """
    import builtins

    real_import = builtins.__import__

    def no_httplib2(name, *args, **kwargs):
        if name in ("google_auth_httplib2", "httplib2"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_httplib2)

    assert google_http.build_kwargs("fake-credentials", 30.0) == {"credentials": "fake-credentials"}


def test_the_calendar_reads_its_timeout_from_config(tmp_path):
    from baton.adapters.cal.google import GoogleCalendar
    from baton.core.config import Config

    config = Config(
        {"calendar": {"google": {"timeout_seconds": 12}}},
        config_file=tmp_path / "baton.yaml",
        profile_dir=tmp_path,
    )

    assert GoogleCalendar.from_config(config).timeout == 12.0
