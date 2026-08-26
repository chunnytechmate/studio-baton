"""The send gate, and everything around it.

The matrix at the heart of this file — pull one required field out at a time —
is the whole point of the phase. A send that quietly goes out incomplete is
the failure mode the original system's gate existed to prevent, and "no
override flag" only means something if it survives contact with tests.
"""

from __future__ import annotations

import json
import sqlite3
import textwrap
from pathlib import Path

import pytest

import baton
from baton.adapters.chat.base import SendOutcome
from baton.adapters.docs.base import Block, DocStatus
from baton.adapters.fakes import FakeDocStore, FakeLearnerStore
from baton.cli.app import run
from baton.domain.models import Learner, Piece
from baton.errors import GateError, NeedsHumanError, UpstreamError
from baton.exits import Exit
from baton.pipelines.send import compose_message, gate_check, gather_context, send_lesson
from baton.pipelines.staging import PieceSnapshot

MIGRATIONS = Path(baton.__file__).resolve().parent / "migrations"

PUBLISHED = {
    "learner_id": "1",
    "learner_name": "Ada Whitfield",
    "session_number": 3,
    "doc_id": "doc-ada-03",
    "doc_url": "https://example.invalid/lesson-3",
    "short_message": "• Covered: Blackbird\n• Progress: Tempo held",
}

STORE = FakeLearnerStore(
    learners=[Learner(id="1", name="Ada Whitfield", instrument="guitar", current_piece_id="2")],
    pieces=[Piece(id="2", title="Blackbird", practice_track="https://example.invalid/track.mp3")],
)

REQUIRED = ["doc_link", "short_summary", "session_number"]
OPTIONAL = ["practice_track", "video_link"]


class FakeMessenger:
    """Records sends; fails on demand."""

    driver = "fake"

    def __init__(self, fail_with: Exception | None = None) -> None:
        self.sent: list[tuple[str, str]] = []
        self.fail_with = fail_with

    def resolve(self, name: str) -> str:
        try:
            return {"teacher": "U-teacher", "me": "U-teacher"}[name]
        except KeyError:
            from baton.errors import NeedsHumanError

            raise NeedsHumanError(
                f'No contact matches "{name}".',
                candidates=[{"name": "teacher"}],
            ) from None

    def send(self, recipient_id: str, text: str) -> SendOutcome:
        if self.fail_with:
            raise self.fail_with
        self.sent.append((recipient_id, text))
        return SendOutcome(sent=True, recipient=recipient_id)

    def health(self) -> None:
        pass


# Gate field names → SendContext attribute names.
def context(**overrides) -> object:
    """A complete context, addressed by *gate* field name so a test can blank
    exactly the field the gate is asked about."""
    from baton.pipelines.send import SendContext

    attributes = {
        "doc_link": "doc_url",
        "short_summary": "short_message",
        "session_number": "session_number",
        "video_link": "video_link",
        "practice_track": "practice_track",
    }
    values = {
        "doc_link": "https://example.invalid/lesson-3",
        "short_summary": "• Covered: Blackbird",
        "session_number": 3,
        "video_link": "https://example.invalid/watch/3",
        "practice_track": "https://example.invalid/track.mp3",
    }
    for key, value in overrides.items():
        values[key] = value
    kwargs = {attributes[key]: value for key, value in values.items()}
    return SendContext(learner_name="Ada Whitfield", doc_id="doc-ada-03", **kwargs)


# -- the gate ----------------------------------------------------------------


@pytest.mark.parametrize("field", ["doc_link", "short_summary", "session_number"])
def test_a_missing_required_field_blocks_the_send(field):
    """One test per field. Each of these is a message that must not go out."""
    with pytest.raises(GateError) as excinfo:
        gate_check(
            context(**{field: ""}),  # type: ignore[arg-type]
            required=REQUIRED,
            optional=OPTIONAL,
        )

    assert [item["field"] for item in excinfo.value.missing] == [field]
    assert excinfo.value.missing[0]["how_to_fix"]


def test_the_block_names_every_missing_field_at_once():
    with pytest.raises(GateError) as excinfo:
        gate_check(
            context(doc_link="", short_summary="", session_number=0),  # type: ignore[arg-type]
            required=REQUIRED,
            optional=OPTIONAL,
        )

    assert {item["field"] for item in excinfo.value.missing} == {
        "doc_link",
        "short_summary",
        "session_number",
    }


def test_the_block_says_nothing_was_sent_and_that_there_is_no_override():
    with pytest.raises(GateError) as excinfo:
        gate_check(context(doc_link=""), required=REQUIRED, optional=OPTIONAL)  # type: ignore[arg-type]

    assert "Nothing was sent" in (excinfo.value.remedy or "")
    assert "no flag" in (excinfo.value.remedy or "").lower()


def test_whitespace_only_values_count_as_missing():
    with pytest.raises(GateError):
        gate_check(
            context(short_summary="   "),  # type: ignore[arg-type]
            required=REQUIRED,
            optional=OPTIONAL,
        )


def test_a_complete_context_passes_and_warns_about_the_optional_gaps():
    _, warnings = gate_check(
        context(video_link="", practice_track=""),  # type: ignore[arg-type]
        required=REQUIRED,
        optional=OPTIONAL,
    )

    assert {item["field"] for item in warnings} == {"video_link", "practice_track"}


def test_zero_is_a_missing_session_number_but_zero_is_valid_nowhere_else():
    with pytest.raises(GateError):
        gate_check(
            context(session_number=0),  # type: ignore[arg-type]
            required=["session_number"],
            optional=[],
        )


def test_the_required_set_is_configuration_not_code():
    """A studio that does not require the document link may drop it; one that
    also requires the recording may add it. The block itself is not negotiable."""
    _, _warnings = gate_check(context(doc_link=""), required=["short_summary"], optional=[])  # type: ignore[arg-type]


# -- composition -------------------------------------------------------------


def test_the_message_is_the_published_one_plus_batons_own_links():
    message = compose_message(context())  # type: ignore[arg-type]

    assert "• Covered: Blackbird" in message
    assert "https://example.invalid/lesson-3" in message
    assert "เฉพาะ Video: https://example.invalid/watch/3" in message


def test_the_recording_line_is_absent_when_there_is_no_recording():
    message = compose_message(context(video_link=""))  # type: ignore[arg-type]

    assert "เฉพาะ Video:" not in message
    assert "https://example.invalid/lesson-3" in message


def test_the_instrument_icon_matches_the_learner():
    message = compose_message(context())  # type: ignore[arg-type]
    from baton.pipelines.send import SendContext

    guitar = compose_message(
        SendContext(
            learner_name="Ada",
            doc_id="d",
            doc_url="u",
            short_message="s",
            session_number=1,
            instrument="กีตาร์",
        )
    )
    drums = compose_message(
        SendContext(
            learner_name="Ada",
            doc_id="d",
            doc_url="u",
            short_message="s",
            session_number=1,
            instrument="กลอง",
        )
    )

    assert message  # a context with no instrument still composes
    assert guitar.startswith("🎸")
    assert drums.startswith("🥁")


def test_titles_appear_only_when_present():
    from baton.pipelines.send import SendContext

    with_title = compose_message(
        SendContext(
            learner_name="Ada",
            doc_id="d",
            doc_url="u",
            short_message="s",
            session_number=1,
            titles="Blackbird",
        )
    )
    without_title = compose_message(
        SendContext(
            learner_name="Ada", doc_id="d", doc_url="u", short_message="s", session_number=1
        )
    )

    assert "🎵 Blackbird" in with_title
    assert "🎵" not in without_title


def test_gathered_context_pulls_the_practice_track_from_the_assigned_piece():
    gathered = gather_context(STORE, "1", PUBLISHED, video_link="v")

    assert gathered.practice_track.endswith("track.mp3")
    assert gathered.learner_name == "Ada Whitfield"


def test_gathered_context_pulls_the_instrument_from_the_learner():
    gathered = gather_context(STORE, "1", PUBLISHED, video_link="v")

    assert gathered.instrument == "guitar"


def test_gathered_context_passes_through_the_documents_date_and_titles():
    gathered = gather_context(STORE, "1", PUBLISHED, date="2026-08-23", titles="Blackbird")

    assert gathered.date == "2026-08-23"
    assert gathered.titles == "Blackbird"


def test_gathered_context_survives_a_learner_with_no_piece():
    bare = FakeLearnerStore(learners=[Learner(id="9", name="No Piece")])

    gathered = gather_context(bare, "9", {**PUBLISHED, "learner_id": "9"})

    assert gathered.practice_track == ""


# -- delivery ----------------------------------------------------------------


def test_a_successful_send_records_the_truth():
    messenger = FakeMessenger()

    result = send_lesson(messenger, STORE, recipient_id="U1", learner_id="1", published=PUBLISHED)

    assert result["sent"] is True
    assert messenger.sent[0][0] == "U1"


def test_dry_run_passes_the_gate_and_sends_nothing():
    messenger = FakeMessenger()

    result = send_lesson(
        messenger, STORE, recipient_id="U1", learner_id="1", published=PUBLISHED, dry_run=True
    )

    assert result["dry_run"] is True
    assert result["sent"] is False
    assert messenger.sent == []


def test_a_refused_delivery_raises_rather_than_returning_sent_false():
    """A platform answering 'no' must not be interpretable as success."""
    messenger = FakeMessenger(fail_with=UpstreamError("line refused", service="line"))

    with pytest.raises(UpstreamError):
        send_lesson(messenger, STORE, recipient_id="U1", learner_id="1", published=PUBLISHED)


# -- contact resolution ------------------------------------------------------


def test_a_partial_contact_name_does_not_resolve():
    from baton.adapters.chat.base import resolve_contact

    (Path("unused"),)
    contacts = {
        "contacts": {
            "office": {"id_env": "BATON_A", "aliases": ["boss"]},
            "office2": {"id_env": "BATON_B", "aliases": []},
        }
    }

    class FakeConfig:
        def section(self, _name):
            return contacts["contacts"]

    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_contact(FakeConfig(), "off")  # type: ignore[arg-type]

    assert excinfo.value.exit_code == Exit.NEEDS_HUMAN


def test_an_alias_resolves_to_its_contact():
    from baton.adapters.chat.base import resolve_contact

    class FakeConfig:
        def section(self, _name):
            return {"office": {"id_env": "BATON_A", "aliases": ["boss", "manager"]}}

        def secret(self, _key, *, required=True):
            return "U123office"

    key, recipient_id = resolve_contact(FakeConfig(), "boss")  # type: ignore[arg-type]

    # The platform id, not the name of the variable holding it: what leaves
    # this function is what a driver puts in the payload.
    assert key == "office"
    assert recipient_id == "U123office"


def test_a_contact_whose_id_env_is_unset_is_a_config_problem():
    from baton.adapters.chat.base import resolve_contact
    from baton.errors import ConfigError

    class FakeConfig:
        def section(self, _name):
            return {"office": {"id_env": "BATON_A", "aliases": []}}

        def secret(self, key, *, required=True):
            raise ConfigError(
                "Environment variable BATON_A is not set.",
                remedy="Export BATON_A (or add it to your .env) and re-run.",
            )

    with pytest.raises(ConfigError) as excinfo:
        resolve_contact(FakeConfig(), "office")  # type: ignore[arg-type]

    assert "BATON_A" in str(excinfo.value)


def test_an_unset_id_env_does_not_leak_the_variable_name_into_a_payload(monkeypatch):
    """End to end for the dereference: a real Config, a real env var."""
    from baton.adapters.chat.base import resolve_contact
    from baton.core.config import Config

    config = Config(
        data={"chat": {"contacts": {"office": {"id_env": "BATON_OFFICE_ID", "aliases": []}}}},
        config_file=Path("baton.yaml"),
        profile_dir=Path("."),
    )

    monkeypatch.delenv("BATON_OFFICE_ID", raising=False)
    with pytest.raises(Exception) as unset:
        resolve_contact(config, "office")
    assert "BATON_OFFICE_ID" in str(unset.value)

    monkeypatch.setenv("BATON_OFFICE_ID", "U8800office")
    key, recipient_id = resolve_contact(config, "office")
    assert (key, recipient_id) == ("office", "U8800office")


def test_a_duplicate_alias_under_one_contact_is_one_match():
    """The same alias written twice is a typo in baton.yaml, not an ambiguity."""
    from baton.adapters.chat.base import resolve_contact

    class FakeConfig:
        def section(self, _name):
            return {"me": {"id_env": "BATON_A", "aliases": ["boss", "boss"]}}

        def secret(self, _key, *, required=True):
            return "U1"

    key, recipient_id = resolve_contact(FakeConfig(), "boss")  # type: ignore[arg-type]

    assert (key, recipient_id) == ("me", "U1")


def test_a_contacts_own_key_wins_over_another_contacts_alias():
    from baton.adapters.chat.base import resolve_contact

    class FakeConfig:
        def section(self, _name):
            return {
                "dad": {"id_env": "BATON_DAD", "aliases": ["mum"]},
                "mum": {"id_env": "BATON_MUM", "aliases": []},
            }

        def secret(self, key, *, required=True):
            return {"chat.contacts.dad.id_env": "Udad", "chat.contacts.mum.id_env": "Umum"}[key]

    key, recipient_id = resolve_contact(FakeConfig(), "mum")  # type: ignore[arg-type]

    assert (key, recipient_id) == ("mum", "Umum")


def test_a_contact_without_an_id_env_is_a_config_problem():
    from baton.adapters.chat.base import resolve_contact

    class FakeConfig:
        def section(self, _name):
            return {"office": {"aliases": ["boss"]}}

    with pytest.raises(NeedsHumanError) as excinfo:
        resolve_contact(FakeConfig(), "office")  # type: ignore[arg-type]

    assert "id_env" in str(excinfo.value)


# -- end to end ----------------------------------------------------------------


@pytest.fixture
def studio(profile, monkeypatch):
    db_path = profile / "data" / "studio.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.executescript((MIGRATIONS / "sqlite.sql").read_text(encoding="utf-8"))
    connection.executescript((MIGRATIONS / "seed_example.sql").read_text(encoding="utf-8"))
    connection.close()

    (profile / "baton.yaml").write_text(
        textwrap.dedent(
            """
            version: 1
            labels:
              learner: student
              session: lesson
            db:
              driver: sqlite
              sqlite:
                path: data/studio.db
            docs:
              driver: notion
            chat:
              driver: line
              contacts:
                teacher:
                  id_env: BATON_TEACHER
                  aliases: [me]
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    docs = FakeDocStore(
        statuses={"doc-ada-03": DocStatus(doc_id="doc-ada-03", status="Complete")},
        blocks={
            "doc-ada-03": [Block(id="v", type="video", url="https://example.invalid/watch/ada-3")]
        },
    )
    monkeypatch.setattr("baton.cli.cmd_send.open_docs", lambda _config: docs)

    messenger = FakeMessenger()
    monkeypatch.setattr("baton.cli.cmd_send.open_chat", lambda _config: messenger)
    monkeypatch.setenv("BATON_TEACHER", "U-teacher")
    return profile, messenger, docs


def publish(profile, learner_id, **overrides):
    """Write a published record directly, as `lesson publish` would have."""
    from datetime import datetime, timezone

    from baton.core import jsonio

    record = {
        **PUBLISHED,
        "learner_id": learner_id,
        "learner_name": overrides.pop("learner_name", "Ada Whitfield"),
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **overrides,
    }
    path = profile / "state" / "published" / f"{learner_id}-{record['session_number']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    jsonio.write_json(path, record)
    return record


def call(studio, *args):
    profile = studio[0]
    return run(["--profile", str(profile), "--json", "send", *args])


def test_a_complete_publishment_sends_through_the_cli(studio, capsys):
    profile, messenger, _docs = studio
    publish(profile, "1")

    assert call(studio, "lesson", "Ada Whitfield", "--to", "teacher") == Exit.OK
    payload = json.loads(capsys.readouterr().out)

    assert payload["sent"] is True
    assert messenger.sent[0][0] == "U-teacher"
    assert "https://example.invalid/watch/ada-3" in messenger.sent[0][1]


def test_dry_run_blocks_before_sending_when_a_field_is_missing(studio, capsys):
    profile, messenger, _docs = studio
    publish(profile, "1", doc_url="")

    assert call(studio, "lesson", "Ada Whitfield", "--to", "teacher", "--dry-run") == Exit.GATE
    payload = json.loads(capsys.readouterr().out)

    assert [m["field"] for m in payload["details"]["missing"]] == ["doc_link"]
    assert messenger.sent == []


def test_sending_for_an_unpublished_learner_is_a_usage_error(studio, capsys):
    assert call(studio, "lesson", "Bruno Castell", "--to", "teacher") == Exit.USAGE

    assert "publish" in json.loads(capsys.readouterr().out)["remedy"]


def test_batch_sends_every_complete_learner_and_reports_the_blocked_one(studio, capsys):
    profile, messenger, _docs = studio
    publish(profile, "1")
    publish(profile, "2", learner_name="Bruno Castell", doc_url="", doc_id="doc-b-1")

    code = call(
        studio,
        "batch",
        "--to",
        "teacher",
        "--learner",
        "Ada Whitfield",
        "--learner",
        "Bruno Castell",
    )

    payload = json.loads(capsys.readouterr().out)
    assert code == Exit.GATE
    assert payload["sent"] == 1
    assert [b["learner"] for b in payload["blocked"]] == ["Bruno Castell"]
    assert len(messenger.sent) == 1


def test_a_batch_rejects_a_duplicated_learner(studio, capsys):
    assert (
        call(
            studio,
            "batch",
            "--to",
            "teacher",
            "--learner",
            "Ada Whitfield",
            "--learner",
            "Ada Whitfield",
        )
        == Exit.USAGE
    )


def test_an_unknown_contact_exits_needs_human(studio, capsys):
    profile = studio[0]
    publish(profile, "1")

    assert call(studio, "lesson", "Ada Whitfield", "--to", "stranger") == Exit.NEEDS_HUMAN

    payload = json.loads(capsys.readouterr().out)
    assert payload["error"] == "needs_human"
    assert "teacher" in [c["name"] for c in payload["details"]["candidates"]]


def test_a_document_outage_does_not_stop_the_gate(studio, capsys):
    """The recording link is optional, so a document-store failure must
    degrade to "no recording line" rather than block the summary. A studio
    that has made video_link required will see the gate block on it instead —
    which is that studio's own standard being enforced, not an outage leaking
    into a different failure."""
    from baton.errors import UpstreamError

    profile = studio[0]
    docs = studio[2]
    publish(profile, "1")
    docs.fail_with = UpstreamError("notion is down", service="notion")

    assert call(studio, "lesson", "Ada Whitfield", "--to", "teacher") == Exit.OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["sent"] is True
    assert "Recording:" not in payload["message"]
    assert {w["field"] for w in payload["warnings"]} >= {"video_link"}


def test_contacts_lists_what_is_configured(studio, capsys):
    assert call(studio, "contacts") == Exit.OK

    payload = json.loads(capsys.readouterr().out)
    assert payload["contacts"][0]["name"] == "teacher"
    assert "me" in payload["contacts"][0]["aliases"]


# -- the webhook driver --------------------------------------------------------


class _Captured:
    """Stands in for http_request, recording what the driver would send."""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.text = ""
        self.kwargs: dict | None = None

    def __call__(self, _method, _url, **kwargs):
        self.kwargs = kwargs
        return self


def test_a_signed_webhook_sends_the_bytes_it_signed(monkeypatch):
    """The signature must be over the exact wire bytes, not a re-serialisation:
    separators and escaping differ between any two serialisations, so signing
    one and sending another verifies nowhere."""
    import hashlib
    import hmac as hmac_mod

    from baton.adapters.chat import drivers

    captured = _Captured()
    monkeypatch.setattr(drivers, "http_request", captured)

    messenger = drivers.WebhookMessenger(
        "https://example.invalid/hook", secret="s3cret", config=None
    )
    messenger.send("ทดสอบ", "สวัสดี")

    assert captured.kwargs is not None
    sent = captured.kwargs["data"]
    assert "json" not in captured.kwargs  # one serialisation, not two
    assert captured.kwargs["headers"]["Content-Type"] == "application/json"

    expected = hmac_mod.new(b"s3cret", sent, hashlib.sha256).hexdigest()
    assert captured.kwargs["headers"]["X-Baton-Signature"] == expected

    import json as json_mod

    assert json_mod.loads(sent) == {"recipient": "ทดสอบ", "text": "สวัสดี"}


def test_an_unsigned_webhook_lets_the_http_layer_serialise(monkeypatch):
    from baton.adapters.chat import drivers

    captured = _Captured()
    monkeypatch.setattr(drivers, "http_request", captured)

    messenger = drivers.WebhookMessenger("https://example.invalid/hook", config=None)
    messenger.send("teacher", "hello")

    assert captured.kwargs["json"] == {"recipient": "teacher", "text": "hello"}
    assert "X-Baton-Signature" not in captured.kwargs["headers"]


def test_a_refused_webhook_reports_the_status(monkeypatch):
    from baton.adapters.chat import drivers

    captured = _Captured(status_code=500)
    monkeypatch.setattr(drivers, "http_request", captured)
    messenger = drivers.WebhookMessenger("https://example.invalid/hook", secret="s", config=None)

    with pytest.raises(UpstreamError) as excinfo:
        messenger.send("teacher", "hello")
    assert "HTTP 500" in str(excinfo.value)


def test_a_connection_error_never_prints_a_bot_token():
    """A Telegram token rides in the URL path; requests embeds the URL in its
    connection errors. One failed connection must not write the credential
    into an error message that ends up on stderr and in job logs."""
    import socket

    from baton.core.retry import http_request, redact

    # A closed port on localhost: refused instantly, no external service touched.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    with pytest.raises(UpstreamError) as excinfo:
        http_request(
            "POST",
            f"https://127.0.0.1:{port}/bot123456:SECRETTOKEN/sendMessage",
            service="telegram",
            timeout=0.2,
            attempts=1,
        )

    assert "SECRETTOKEN" not in str(excinfo.value)
    assert "/bot***/sendMessage" in str(excinfo.value)
    assert redact("https://x.invalid/bot1:abc?token=zzz&api_key=qqq") == (
        "https://x.invalid/bot***?token=***&api_key=***"
    )


# -- the practice track belongs to the lesson, not to today (#29) ----------------

MOVED_ON = FakeLearnerStore(
    learners=[Learner(id="1", name="Ada Whitfield", instrument="guitar", current_piece_id="9")],
    pieces=[
        Piece(id="2", title="Blackbird", practice_track="https://example.invalid/track.mp3"),
        Piece(id="9", title="Michelle", practice_track="https://example.invalid/newer.mp3"),
    ],
)

TAUGHT = Piece(id="2", title="Blackbird", practice_track="https://example.invalid/track.mp3")


def _published_with(snapshot: PieceSnapshot) -> dict:
    return {**PUBLISHED, "piece_snapshot": snapshot.to_dict()}


def test_the_snapshot_wins_over_the_learners_newer_piece():
    """The drift this exists to stop: published Monday, sent Friday, after the
    learner moved to another song."""
    gathered = gather_context(MOVED_ON, "1", _published_with(PieceSnapshot.capture(TAUGHT)))

    assert gathered.practice_track == "https://example.invalid/track.mp3"


def test_a_snapshot_of_no_piece_sends_no_track_even_when_one_exists_now():
    """`none` is information, not a gap: the lesson was taught without a piece
    assigned, so there is no track that belongs to it."""
    gathered = gather_context(MOVED_ON, "1", _published_with(PieceSnapshot.capture(None)))

    assert gathered.practice_track == ""


def test_a_record_written_before_snapshots_still_falls_back_to_the_live_piece():
    """Records published by an older Baton carry no snapshot at all. A stale
    track is closer to the truth than dropping the line entirely."""
    assert "piece_snapshot" not in PUBLISHED

    gathered = gather_context(MOVED_ON, "1", PUBLISHED)

    assert gathered.practice_track == "https://example.invalid/newer.mp3"


def test_a_snapshot_whose_piece_lost_its_track_is_not_backfilled_from_today():
    """A captured piece with an empty track means the piece had none then —
    reaching for the current one would reintroduce the drift by the back door."""
    trackless = Piece(id="2", title="Blackbird", practice_track="")

    gathered = gather_context(MOVED_ON, "1", _published_with(PieceSnapshot.capture(trackless)))

    assert gathered.practice_track == ""
