"""Google calls get a deadline like everything else Baton talks to.

`core.retry.http_request` forces a timeout onto every `requests` call and the
encoder kills ffmpeg on its own clock, but `googleapiclient` builds its
transport from `httplib2.Http()`, whose timeout defaults to the socket default
— which is `None`. A Drive listing that never gets an answer never returned,
and under a harness that reads as a shell command hanging until it is killed,
with a booking that may or may not have been made and nothing written down.
"""

from __future__ import annotations

import pytest

from baton.adapters import google_http


def test_a_timeout_produces_an_authorized_transport():
    # Skipped on a core install: `google-auth-httplib2` arrives with the
    # `google` extra, and CI's test job installs only `dev`. The behaviour when
    # it is missing has its own test below — that path is the one a core
    # install actually takes.
    pytest.importorskip("google_auth_httplib2")

    kwargs = google_http.build_kwargs("fake-credentials", 30.0)

    assert "http" in kwargs
    # `build()` refuses both at once, so the deadline has to arrive *through*
    # the credentials rather than beside them.
    assert "credentials" not in kwargs
    assert kwargs["http"].http.timeout == 30.0


@pytest.mark.parametrize("timeout", [None, 0, -1])
def test_no_timeout_falls_back_to_plain_credentials(timeout):
    assert google_http.build_kwargs("fake-credentials", timeout) == {
        "credentials": "fake-credentials"
    }


def test_a_missing_helper_library_is_not_fatal(monkeypatch):
    """A core install has no `google-auth-httplib2`, and that must still work.

    It ships with the API client, so on a `[google]` install it is always
    there. Anywhere else — including CI's own test job — the deadline is
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
