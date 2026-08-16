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

    assert message.startswith("• Covered: Blackbird")
    assert "Lesson notes: https://example.invalid/lesson-3" in message
    assert "Recording: https://example.invalid/watch/3" in message


def test_the_recording_line_is_absent_when_there_is_no_recording():
    message = compose_message(context(video_link=""))  # type: ignore[arg-type]

    assert "Recording:" not in message
    assert "Lesson notes:" in message


def test_gathered_context_pulls_the_practice_track_from_the_assigned_piece():
    gathered = gather_context(STORE, "1", PUBLISHED, video_link="v")

    assert gathered.practice_track.endswith("track.mp3")
    assert gathered.learner_name == "Ada Whitfield"


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

    key, env_name = resolve_contact(FakeConfig(), "boss")  # type: ignore[arg-type]

    assert key == "office"
    assert env_name == "BATON_A"


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
