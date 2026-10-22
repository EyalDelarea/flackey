"""Report this failure (issue #165): the diagnosis line, the request's record, and the log narrowed to it."""
from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from flackey.models import (
    ATTEMPT_OUTCOMES,
    MISS_REASON,
    LosslessAttempt,
    Rejection,
    Request,
    RequestKind,
    RequestState,
)
from flackey.store import Store
from flackey.web.failure import (
    ATTEMPT_PHRASE,
    DIAGNOSIS_CHARS,
    FailureRecord,
    attempt_line,
    diagnose,
    load,
    narrow_log,
    record_text,
)
from flackey.web.report import ClientContext, Redactor, build_report, compose_email
from tests.test_report import APP, HOME, app, settings_for  # noqa: F401  (`app` is a fixture)

T0 = "2026-10-20T10:00:00+00:00"
T1 = "2026-10-20T10:05:00+00:00"


def request(state: RequestState, **kw) -> Request:
    return Request(**{"id": 7, "created_at": T0, "updated_at": T1, "raw_text": "https://youtu.be/abc",
                      "kind": RequestKind.YT_TRACK, "state": state, **kw})


def attempt(outcome: str | None, *, survivors: int | None = None, seen: int | None = None, events=(),
            aid: int = 1) -> LosslessAttempt:
    report = None if survivors is None and seen is None else {
        "seen": seen if seen is not None else survivors, "survivors": [{}] * (survivors or 0), "summary": "s"}
    timeline = [{"t_ms": 10 * i, "event": name, "detail": detail} for i, (name, detail) in enumerate(events)]
    return LosslessAttempt(id=aid, request_id=7, provider="soulseek", created_at=T0, query="Artist Title",
                           outcome=outcome, report=report, timeline=timeline)


def rejection(reason: str, cutoff_hz: int | None, kind: str = "quality") -> Rejection:
    return Rejection(id=1, request_id=7, reason=reason, bitrate_kbps=320, cutoff_hz=cutoff_hz,
                     spectrogram_path=None, created_at=T1, kind=kind)


# ---- the diagnosis --------------------------------------------------------------------------------

def test_every_attempt_outcome_has_a_diagnosis():
    """An outcome with no phrase would reach the line as its bare identifier -- readable, but the one
    category nobody chose the words for."""
    assert set(ATTEMPT_PHRASE) == set(ATTEMPT_OUTCOMES)


@pytest.mark.parametrize("rec, want", [
    (FailureRecord(request(RequestState.REJECTED),
                   rejections=[rejection("cutoff 16000 Hz below 19000: upsampled from a lower bitrate", 16000)]),
     "Rejected: quality check (edge at 16.0 kHz)"),
    (FailureRecord(request(RequestState.REJECTED), rejections=[rejection("bitrate 128 kbps below 320", 0)]),
     "Rejected: quality check (bitrate 128 kbps below 320)"),
    (FailureRecord(request(RequestState.REJECTED), rejections=[
        rejection("cutoff 15000 Hz below 19000", 15000),
        rejection("a different recording: best score 0.41 below 0.60", None, kind="different_recording")]),
     "Rejected: a different recording (best score 0.41 below 0.60)"),
    (FailureRecord(request(RequestState.NOT_FOUND, error_message="could not identify this track: no Deezer "
                           "candidates and no Beatport match")),
     "Not found: could not identify this track: no Deezer candidates and no Beatport match"),
    (FailureRecord(request(RequestState.ERROR, failed_stage="search", error_message="source error: timed out")),
     "Error at search: source error: timed out"),
    (FailureRecord(request(RequestState.ERROR, error_message="boom")), "Error at an unrecorded stage: boom"),
    (FailureRecord(request(RequestState.CANCELLED)), "Not a failure: this track is cancelled now"),
], ids=["quality", "quality-no-cutoff", "latest-rejection", "not-found", "error-stage", "error-no-stage",
        "not-a-failure"])
def test_the_diagnosis_names_the_failure_the_way_the_app_does(rec, want):
    assert diagnose(rec) == want


def no_route(outcome: str) -> str:
    """What `_no_route` writes on the row: MISS_REASON's sentence, inside its own."""
    return (f"no way to fetch this track: {MISS_REASON[outcome]}, Deezer offered nothing to fall back on. "
            "Use Try again in the app to search once more")


@pytest.mark.parametrize("a, want", [
    (attempt("transfer_failed", survivors=4, events=[("enqueue", {})] * 3),
     "Soulseek: 4 copies found, 3 tried, the transfers failed"),
    (attempt("no_pick", seen=0), "Soulseek returned 0 results"),
    (attempt("no_pick", seen=37, survivors=0), "Soulseek: 37 results, none matched closely enough"),
    (attempt("fingerprint_failed", survivors=1, events=[("enqueue", {})]),
     "Soulseek: 1 copy found, 1 tried, a different recording"),
    (attempt("transfer_failed", survivors=2, events=[("disk_full", {"pick": 1})]),
     "Soulseek: 2 copies found, the disk was full"),
    (attempt("no_pick", events=[("search_failed", {"error": "search rejected: http 500"})]),
     "Soulseek: the search failed (search rejected: http 500)"),
    (attempt("unavailable"), "Soulseek: not reachable"),
    (attempt("no_pick"), "Soulseek: no result matched closely enough"),
], ids=["transfers", "zero-results", "no-match", "one-copy", "disk-full", "search-failed", "unavailable",
        "no-count"])
def test_a_soulseek_miss_is_diagnosed_from_its_attempt(a, want):
    rec = FailureRecord(request(RequestState.ERROR, failed_stage="download", error_message=no_route(a.outcome)),
                        attempts=[a])
    assert diagnose(rec) == want


def test_the_long_wait_giving_up_is_still_a_soulseek_miss():
    """`_wait_for_soulseek` words its last error differently from `_no_route`, around the same sentence."""
    msg = f"{MISS_REASON['queued']}; looked 12 times over 72 h. Use Try again in the app to search once more"
    rec = FailureRecord(request(RequestState.ERROR, failed_stage="download", error_message=msg),
                        attempts=[attempt("no_pick", seen=0, aid=1),
                                  attempt("queued", survivors=5, events=[("enqueue", {})], aid=2)])
    assert diagnose(rec) == "Soulseek: 5 copies found, 1 tried, stuck in the peers' queues (last of 2 attempts)"


def test_an_error_that_is_not_the_soulseek_miss_is_not_blamed_on_it():
    """A Deezer fetch that broke after Soulseek missed is a Deezer error, whatever the attempt row says."""
    rec = FailureRecord(request(RequestState.ERROR, failed_stage="download", error_message="source error: bot gone"),
                        attempts=[attempt("no_pick", seen=0)])
    assert diagnose(rec) == "Error at download: source error: bot gone"


def test_not_found_says_why_even_when_an_earlier_pass_searched_soulseek():
    """not_found is only ever written when there was nothing to search with, so an attempt row on the request
    belongs to an earlier pass and must not stand in for this one's reason."""
    rec = FailureRecord(request(RequestState.NOT_FOUND, error_message="could not identify this track: no Deezer "
                                "candidates and no Beatport match"),
                        attempts=[attempt("transfer_failed", survivors=5, events=[("enqueue", {})])])
    assert diagnose(rec) == "Not found: could not identify this track: no Deezer candidates and no Beatport match"


def test_the_diagnosis_is_redacted_before_it_is_cut():
    """A secret straddling the length cap must not go out as its first half."""
    secret = "telegram-hash-0123456789abcdef"
    msg = "x" * 130 + f" {secret} mail me@example.com from {Path.home()}/Music"
    rec = FailureRecord(request(RequestState.ERROR, failed_stage="download", error_message=msg))
    line = diagnose(rec, Redactor([secret], home=Path.home()))
    assert "telegram-hash" not in line and len(line) <= DIAGNOSIS_CHARS


def test_the_diagnosis_subject_and_body_carry_no_secrets(tmp_path: Path):
    secret = "slskd-key-0123456789abcdef"
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    rid = store.add_request("Artist - Title", RequestKind.TEXT)
    msg = "source error: " + "y" * 135 + f" {secret} reply to me@example.com, see {Path.home()}/x.log"
    store.update_request(rid, state=RequestState.ERROR, failed_stage="search", error_message=msg)
    redact = Redactor([secret], home=Path.home())
    rep = build_report(settings, {}, ClientContext(), redact=redact, failure=load(store, rid), log_tz=UTC)
    email = compose_email(rep, "", "", "z.zip", redact)
    for text in (rep.diagnosis, email.subject, email.body, rep.files["report.txt"]):
        for leaked in ("slskd-key-0", "me@example.com", str(Path.home())):
            assert leaked not in text, (leaked, text)


def test_the_diagnosis_is_one_short_line():
    rec = FailureRecord(request(RequestState.ERROR, failed_stage="verify", error_message="x\n" * 400))
    line = diagnose(rec)
    assert len(line) <= DIAGNOSIS_CHARS and line.endswith("x…") and "\n" not in line


def test_an_unfinished_attempt_says_so():
    assert attempt_line(attempt(None)) == "Soulseek: the attempt never finished"


# ---- the log, narrowed --------------------------------------------------------------------------------

LOG = """\
2026-10-20 09:50:00.000 WARNING flackey.worker.core: Soulseek dropped long before
2026-10-20 10:00:01.000 DEBUG   flackey.worker.core: req#7 queued -> identifying Title
2026-10-20 10:00:02.000 DEBUG   flackey.worker.core: req#70 queued -> identifying Other
2026-10-20 10:00:03.000 INFO    flackey.attempts: req=7 slsk=11 search_started query=Artist Title
2026-10-20 10:00:04.000 INFO    flackey.attempts: req=17 slsk=12 search_started query=Someone Else
2026-10-20 10:00:05.000 INFO    flackey.source.slskd: removed stale file at derived path: x.flac
2026-10-20 10:01:00.000 WARNING flackey.worker.core: soulseek: health probe failed
Traceback (most recent call last):
  File "core.py", line 1, in probe
ConnectionError: refused
2026-10-20 10:02:00.000 WARNING flackey.worker.acoustic: req#8: no video reference: gone
2026-10-20 10:03:00.000 ERROR   flackey.worker.core: req#7 failed
Traceback (most recent call last):
ValueError: bad
2026-10-20 10:04:00.000 INFO    uvicorn.access: 127.0.0.1:5 - "POST /api/requests/7/retry HTTP/1.1" 200
2026-10-20 10:04:01.000 INFO    uvicorn.access: 127.0.0.1:5 - "POST /api/requests/77/retry HTTP/1.1" 200
2026-10-20 11:00:00.000 ERROR   flackey.worker.core: something unrelated, an hour later
"""


def test_the_log_keeps_the_request_s_own_lines_and_the_warnings_around_it():
    start, end = datetime(2026, 10, 20, 9, 58, tzinfo=UTC), datetime(2026, 10, 20, 10, 7, tzinfo=UTC)
    kept = narrow_log(LOG, 7, start, end, tz=UTC)
    assert kept == """\
2026-10-20 10:00:01.000 DEBUG   flackey.worker.core: req#7 queued -> identifying Title
2026-10-20 10:00:03.000 INFO    flackey.attempts: req=7 slsk=11 search_started query=Artist Title
2026-10-20 10:01:00.000 WARNING flackey.worker.core: soulseek: health probe failed
Traceback (most recent call last):
  File "core.py", line 1, in probe
ConnectionError: refused
2026-10-20 10:03:00.000 ERROR   flackey.worker.core: req#7 failed
Traceback (most recent call last):
ValueError: bad
2026-10-20 10:04:00.000 INFO    uvicorn.access: 127.0.0.1:5 - "POST /api/requests/7/retry HTTP/1.1" 200
"""


def test_the_log_s_local_time_is_compared_in_its_own_zone():
    """The store writes UTC and the log writes the machine's clock: a request at 10:00 UTC logged in
    UTC+3 is at 13:00 in the file, and a window that ignored the difference would miss every warning."""
    plus3 = timezone(timedelta(hours=3))
    line = "2026-10-20 13:01:00.000 WARNING flackey.worker.core: soulseek: health probe failed\n"
    start, end = datetime(2026, 10, 20, 9, 58, tzinfo=UTC), datetime(2026, 10, 20, 10, 7, tzinfo=UTC)
    assert narrow_log(line, 7, start, end, tz=plus3) == line
    assert narrow_log(line, 7, start, end, tz=UTC) == ""


def test_the_log_is_read_in_this_machine_s_own_zone_by_default():
    """The real path: no zone given, the log's stamps read as the machine's local time, as `logging` wrote them."""
    at = datetime(2026, 10, 20, 10, 1, tzinfo=UTC)
    stamp = at.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    line = f"{stamp}.000 WARNING flackey.worker.core: soulseek: health probe failed\n"
    assert narrow_log(line, 7, at - timedelta(minutes=1), at + timedelta(minutes=1)) == line
    assert narrow_log(line, 7, at + timedelta(minutes=5), at + timedelta(minutes=9)) == ""


# ---- the record, and the report built from it ------------------------------------------------------

@pytest.fixture
def failed(tmp_path: Path):
    """A request that searched Soulseek twice and gave up, as the worker leaves it in the store."""
    settings = settings_for(tmp_path)
    store = Store(settings.db_path)
    rid = store.add_request("https://www.youtube.com/watch?v=abc", RequestKind.YT_TRACK,
                            source_url="https://www.youtube.com/watch?v=abc")
    for query, outcome, events in [
            ("Artist Title", "no_pick", [("search_started", {"query": "Artist Title"}),
                                         ("search_completed", {"files": 0, "peers": 0})]),
            ("Artist Title Extended", "transfer_failed",
             [("search_completed", {"files": 9, "peers": 4}), ("enqueue", {"pick": 1, "peer": "dj_x"}),
              ("transfer_failed", {"error": f"refused by 84.12.3.4 at {Path.home()}/x"})])]:
        aid = store.add_attempt(rid, "soulseek", query)
        timeline = [{"t_ms": 1500 * i, "event": e, "detail": d} for i, (e, d) in enumerate(events)]
        store.update_attempt(aid, outcome=outcome, timeline=timeline, total_ms=4500,
                             report={"seen": 9, "survivors": [{"username": "dj_x"}] * 2, "summary": "9 files: 7 bitrate"}
                             if outcome != "no_pick" else {"seen": 0, "survivors": [], "summary": "0 files: nothing left"})
    store.update_request(rid, state=RequestState.ERROR, failed_stage="download", attempts=4,
                         query_artist="Artist", query_title="Title",
                         error_message=no_route("transfer_failed") + " -- key slskd-key-9876")
    store.conn.execute("UPDATE requests SET created_at=?, updated_at=? WHERE id=?", (T0, T1, rid))
    store.conn.commit()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "flackey.log.3").write_text(
        f"2026-10-20 10:00:01.000 DEBUG   flackey.worker.core: req#{rid} queued -> identifying Title\n", encoding="utf-8")
    (settings.data_dir / "flackey.log").write_text(
        f"2026-10-20 10:04:00.000 INFO    flackey.attempts: req={rid} slsk=2 outcome=transfer_failed total_ms=4500\n"
        f"2026-10-20 10:04:01.000 INFO    flackey.worker.core: req#{rid + 1} filed Someone – Else\n", encoding="utf-8")
    return settings, store, rid


def test_the_record_reads_as_text(failed):
    _, store, rid = failed
    text = record_text(load(store, rid))
    assert text.startswith(f"This track (request {rid})\nLink: https://www.youtube.com/watch?v=abc\nKind: yt_track\n")
    for line in ("Asked for: Artist – Title", "Matched: none", "State: error", "Failed stage: download",
                 "Attempts: 4", "Fetch source: none", f"Created: {T0}", f"Updated: {T1}", "Rejections: none",
                 "Lossless attempts (2)", '1. #1 soulseek, ', 'query "Artist Title"',
                 "outcome no_pick; first byte none; total 4.5 s", "Soulseek returned 0 results",
                 "Soulseek: 2 copies found, 1 tried, the transfers failed", "pick: 9 files: 7 bitrate",
                 "+1.5 s enqueue pick=1 peer=dj_x"):
        assert line in text, line


def test_a_failure_report_carries_the_record_its_rows_and_its_own_log_lines(failed):
    settings, store, rid = failed
    rep = build_report(settings, {}, ClientContext(screen="Download"), failure=load(store, rid),
                       redact=Redactor(["slskd-key-9876"], home=Path.home()), log_tz=UTC)
    assert rep.diagnosis == "Soulseek: 2 copies found, 1 tried, the transfers failed (last of 2 attempts)"
    assert set(rep.files) == {"report.txt", f"request-{rid}.json", f"request-{rid}.log"}
    txt = rep.files["report.txt"]
    assert txt.splitlines()[1] == f"Diagnosis: {rep.diagnosis}"
    assert "Failed track: Artist – Title" in txt and "This track (request" in txt
    # The oldest rotated file is read too, and another request's line is not.
    assert rep.files[f"request-{rid}.log"].splitlines() == [
        f"2026-10-20 10:00:01.000 DEBUG   flackey.worker.core: req#{rid} queued -> identifying Title",
        f"2026-10-20 10:04:00.000 INFO    flackey.attempts: req={rid} slsk=2 outcome=transfer_failed total_ms=4500"]
    assert rep.log_lines == 2
    rows = json.loads(rep.files[f"request-{rid}.json"])
    assert [a["outcome"] for a in rows["attempts"]] == ["no_pick", "transfer_failed"]
    assert rows["attempts"][1]["timeline"][1]["event"] == "enqueue"
    everything = "".join(rep.files.values())
    for leaked in ("slskd-key-9876", "84.12.3.4", str(Path.home())):
        assert leaked not in everything


def test_a_request_the_log_has_moved_past_says_so(failed):
    settings, store, rid = failed
    for p in settings.data_dir.glob("flackey.log*"):
        p.unlink()
    rep = build_report(settings, {}, ClientContext(), failure=load(store, rid), log_tz=UTC)
    assert "No log lines about this track are left" in rep.files[f"request-{rid}.log"]


def test_the_failure_email_leads_with_the_diagnosis(failed):
    settings, store, rid = failed
    rep = build_report(settings, {}, ClientContext(), failure=load(store, rid), log_tz=UTC)
    email = compose_email(rep, "", "", "flackey-bug-report-1.zip", Redactor([], home=HOME))
    assert email.subject == f"Flackey failure: {rep.diagnosis}"
    assert email.body.startswith(f"Diagnosis: {rep.diagnosis}\n\n-- Details from Flackey --")
    assert "this track's record and its log" in email.body
    worded = compose_email(rep, "It was on Soulseek yesterday", "", "z.zip", Redactor([], home=HOME))
    assert worded.body.startswith(f"Diagnosis: {rep.diagnosis}\n\nIt was on Soulseek yesterday\n")


# ---- the endpoints ----------------------------------------------------------------------------------

def failed_request(client, settings) -> int:
    store = Store(settings.db_path)
    rid = store.add_request("Artist - Title", RequestKind.TEXT)
    store.add_rejection(rid, "cutoff 16000 Hz below 19000: upsampled from a lower bitrate", 320, 16000, None)
    store.update_request(rid, state=RequestState.REJECTED)
    return rid


def test_a_failure_preview_and_send_need_no_words_of_the_reporter_s_own(app):  # noqa: F811
    client, settings, revealed, opened = app
    rid = failed_request(client, settings)
    preview = client.post("/api/bug-report/preview", json={"screen": "download", "request_id": rid}).json()
    assert preview["diagnosis"] == "Rejected: quality check (edge at 16.0 kHz)"
    assert {f["name"] for f in preview["files"]} == {"report.txt", f"request-{rid}.json", f"request-{rid}.log"}
    res = client.post("/api/bug-report", json={"screen": "download", "request_id": rid}, headers=APP)
    assert res.status_code == 200
    out = res.json()
    assert out["subject"] == "Flackey failure: Rejected: quality check (edge at 16.0 kHz)"
    assert len(revealed) == 1 and len(opened) == 1
    with zipfile.ZipFile(settings.data_dir / "bug-reports" / out["file"]) as zf:
        assert "Diagnosis: Rejected: quality check (edge at 16.0 kHz)" in zf.read("report.txt").decode()
        assert f"request-{rid}.log" in zf.namelist() and "flackey.log" not in zf.namelist()


@pytest.mark.parametrize("rid, status", [(999, 404), ("7", 400), (True, 400), (0, 400), (2**63, 400)])
def test_a_failure_report_for_no_such_track_is_refused(app, rid, status):  # noqa: F811
    client, _, revealed, opened = app
    assert client.post("/api/bug-report/preview", json={"request_id": rid}).status_code == status
    assert client.post("/api/bug-report", json={"request_id": rid}, headers=APP).status_code == status
    assert revealed == [] and opened == []


def test_the_bug_report_is_unchanged_without_a_request(app):  # noqa: F811
    client, _, _, _ = app
    preview = client.post("/api/bug-report/preview", json={"screen": "download"}).json()
    assert preview["diagnosis"] is None
    assert {f["name"] for f in preview["files"]} == {"report.txt", "flackey.log", "flackey.log.1"}

