"""The worker's lossless step with a fake provider. Every test asserts the attempt row's outcome, that the
timeline ends with the outcome event, that tmp_dir is empty afterwards, and which source the file came from."""
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from krater import worker as worker_mod
from krater.config import Settings
from krater.convert import ConvertError
from krater.fingerprint import FingerprintResult
from krater.lossless import LosslessFile
from krater.models import CatalogTrack, RequestKind, RequestState
from krater.notify import MemoryNotifier
from krater.source import LosslessError, SourceTimeout, TransferProgress
from krater.store import Store
from krater.worker import Worker, format_line
from tests.conftest import requires_ffmpeg
from tests.test_worker import CT, TEXT, FakeCatalog, FakeSource, _mp3, good_cand, no_art

pytestmark = requires_ffmpeg
CT3 = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})


def _flac(path: Path, seconds: int = 3) -> Path:
    # -sample_fmt s16: anoisesrc is float and ffmpeg's flac encoder otherwise picks 24-bit for it; this is
    # meant to be the 16-bit CD-quality file `lf()` describes.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"anoisesrc=color=white:seed=2:sample_rate=44100:duration={seconds}", "-ac", "2", "-c:a", "flac",
                    "-sample_fmt", "s16", str(path)], check=True)
    return path


def _fake_flac(path: Path) -> Path:
    """A lossy source inside a FLAC container: verify rejects it (cutoff far below 20 kHz)."""
    mp3 = _mp3(path.with_suffix(".mp3"), kbps=64)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-c:a", "flac", str(path)], check=True)
    mp3.unlink()
    return path


def lf(username: str = "a", **kw) -> LosslessFile:
    base = {"provider": "soulseek", "username": username,
            "path": "Void\\02. Astral Projection - Into the Void.flac", "extension": "flac", "size": 320_000,
            "length_s": 3, "bitrate_kbps": None, "sample_rate": 44100, "bit_depth": 16, "has_free_slot": True,
            "upload_speed_bps": 2_000_000, "queue_length": 0}
    base.update(kw)
    return LosslessFile(**base)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeProvider:
    name = "soulseek"

    def __init__(self, downloads: Path, files=None, *, audio: dict[str, Path] | None = None, health="ok",
                 search_error=None, download_error=None):
        self.downloads, self.files, self.audio = downloads, files or [], audio or {}
        self.health_status, self.search_error, self.download_error = health, search_error, download_error
        self.searches, self.downloaded, self.cancelled, self.rescans = [], [], 0, 0

    async def health(self):
        return {"status": self.health_status, "username": "krater-dj"}

    async def search(self, text, *, wait_s, on_raw=None):
        self.searches.append(text)
        if self.search_error:
            raise self.search_error
        if on_raw:
            on_raw("search", {"state": "Completed, TimedOut", "fileCount": len(self.files)})
            on_raw("responses", [{"username": f.username, "files": [{"filename": f.path, "size": f.size}]} for f in self.files])
        return list(self.files)

    async def download(self, file, *, first_byte_s, total_s, poll_s, on_progress=None, on_raw=None):
        self.downloaded.append(file.username)
        if on_progress:
            on_progress(TransferProgress("Queued, Remotely", 0, file.size, 0.0, None))
        if self.download_error:
            raise self.download_error
        dest = self.downloads / "Void" / file.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.audio[file.username], dest)
        if on_progress:
            on_progress(TransferProgress("InProgress", 10, file.size, 1e6, 800))
            on_progress(TransferProgress("Completed, Succeeded", file.size, file.size, 1e6, 800))
        if on_raw:
            on_raw("transfer-0", {"state": "Completed, Succeeded"})
        return dest

    async def cancel_all(self):
        self.cancelled += 1
        return 0

    async def rescan_shares(self):
        self.rescans += 1


@pytest.fixture
def lenv(tmp_path: Path, monkeypatch):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h", library_root=tmp_path / "lib",
                        data_dir=tmp_path / "data", slskd_api_key="k", lossless_poll_s=0.01, lossless_search_wait_s=5,
                        lossless_first_byte_s=10, lossless_transfer_s=20)
    store = Store(settings.db_path)
    downloads = settings.slskd_downloads
    downloads.mkdir(parents=True)
    good = _flac(tmp_path / "good.flac")
    provider = FakeProvider(downloads, [lf("a")], audio={"a": good, "b": good})
    matched = FingerprintResult("matched", 0.98, 12.3, "preview found at 12.3 s, score 0.98", [1, 2, 3], [4, 5, 6])

    async def fake_check(path, deezer_id, http, *, minimum, tmp_dir):
        return fake_check.result

    fake_check.result = matched
    monkeypatch.setattr(worker_mod, "fingerprint_check", fake_check)
    return settings, store, MemoryNotifier(), provider, fake_check, Clock()


def make(lenv, provider=None, source=None, catalog=None, **kw):
    settings, store, notifier, default_provider, _, clock = lenv
    providers = kw.pop("providers", [provider or default_provider])
    return Worker(store, source or FakeSource([good_cand()]), catalog or FakeCatalog([CT3]), notifier, settings,
                  artwork_fetch=no_art, providers=providers, http=httpx.AsyncClient(), clock=clock, **kw)


def attempt_of(store: Store, rid: int):
    a = store.get_attempt_for_request(rid)
    assert a is not None and a.timeline[-1]["event"] == "outcome" and a.timeline[-1]["detail"]["outcome"] == a.outcome
    return a


async def test_hit_files_wav_with_evidence_done_line_and_raw_record(lenv):
    settings, store, notifier, _, _, _ = lenv
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and r.fetch_source is None
    t = store.get_track(r.track_id)
    assert t.path.suffix == ".wav" and t.path.exists() and (t.source, t.source_fmt, t.bit_depth, t.sample_rate) == (
        "soulseek", "flac", 16, 44100)
    assert t.fmt == "wav"
    assert w.source.fetched == []                               # Deezer never asked
    a = attempt_of(store, rid)
    assert a.outcome == "filed" and a.first_byte_ms == 800 and a.total_ms is not None
    assert a.report["chosen"]["username"] == "a" and a.fingerprint["status"] == "matched"
    assert [e["event"] for e in a.timeline][:7] == ["search_started", "search_completed", "pick", "enqueue",
                                                     "transfer_state", "transfer_state", "first_byte"]
    assert {"verify", "fingerprint", "convert"} <= {e["event"] for e in a.timeline}
    assert sorted(p.name for p in Path(a.raw_dir).iterdir()) == ["01-search.json", "02-responses.json", "03-transfer-0.json",
                                                                  "04-fingerprint.json"]
    assert not list(settings.tmp_dir.iterdir())
    assert not list(settings.slskd_downloads.rglob("*.flac"))   # moved out of the sidecar's folder
    kinds = {e.kind: e.value for e in store.list_evidence(t.id)}
    assert kinds["source"] == {"provider": "soulseek", "source_fmt": "flac", "attempt_id": a.id}
    assert kinds["recording_match"]["score"] == 0.98 and kinds["recording_match"]["reference"] == "deezer:1754956977"
    assert kinds["fingerprint"]["frames"] == [4, 5, 6]
    done = notifier.sent[-1][0]
    assert "WAV 16-bit/44.1 kHz, from FLAC via Soulseek" in done and "content to" in done and "kHz" in done
    assert store.stats()["by_source"] == {"soulseek": 1}
    assert w.status["lossless_provider"] == {"name": "soulseek", "status": "ok", "username": "krater-dj"}


async def test_no_pick_falls_back_to_deezer(lenv):
    settings, store, notifier, provider, _, _ = lenv
    provider.files = [lf("a", extension="mp3")]
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and w.source.fetched == ["dz_track:1754956977:send"]
    assert attempt_of(store, rid).outcome == "no_pick" and provider.downloaded == []
    t = store.get_track(r.track_id)
    assert (t.source, t.source_fmt, t.path.suffix) == ("deezer_bot", None, ".mp3")
    assert "MP3 320 kbps via Deezer" in notifier.sent[-1][0]
    assert not list(settings.tmp_dir.iterdir()) and len(store.get_candidates(rid)) == 1


async def test_provider_down_is_unavailable_and_costs_no_search(lenv):
    _, store, _, provider, _, _ = lenv
    provider.health_status = "unreachable"
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and provider.searches == [] and attempt_of(store, rid).outcome == "unavailable"
    assert w.status["lossless_provider"]["status"] == "unreachable"


@pytest.mark.parametrize("outcome", ["first_byte_timeout", "transfer_timeout", "transfer_failed"])
async def test_download_failures_fall_back_with_their_outcome(lenv, outcome):
    settings, store, _, provider, _, _ = lenv
    provider.files = [lf("a")]
    provider.download_error = LosslessError("boom", outcome)
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and store.get_track(r.track_id).source == "deezer_bot"
    a = attempt_of(store, rid)
    assert a.outcome == outcome and provider.downloaded == ["a"]
    assert not list(settings.tmp_dir.iterdir())


@pytest.mark.parametrize("outcome", ["first_byte_timeout", "transfer_failed"])
async def test_a_peer_that_will_not_send_moves_on_to_the_next_one(lenv, outcome):
    """A refused or never-started transfer is a fact about that peer, not about the file, so the
    survivors behind it still deserve a turn (SECOND_PICK_AFTER)."""
    settings, store, _, provider, _, _ = lenv
    provider.files = [lf("a"), lf("b")]
    provider.download_error = LosslessError("boom", outcome)
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    await w.process(rid)
    a = attempt_of(store, rid)
    assert a.outcome == outcome and provider.downloaded == ["a", "b"]
    assert not list(settings.tmp_dir.iterdir())


async def test_transfer_timeout_stops_at_that_peer(lenv):
    """The one download failure that does *not* try again: bytes were flowing and merely too slow,
    which says something about the link rather than the peer, so the next one is no more likely."""
    _, store, _, provider, _, _ = lenv
    provider.files = [lf("a"), lf("b")]
    provider.download_error = LosslessError("boom", "transfer_timeout")
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    await w.process(rid)
    assert attempt_of(store, rid).outcome == "transfer_timeout" and provider.downloaded == ["a"]


async def test_a_rejected_transfer_still_files_from_the_next_peer(lenv):
    """The case measured on 2026-09-09: request 1 ("Hallucinogen - LSD") chose a free-slot FLAC and the
    peer answered `Completed, Rejected` two seconds later. Before SECOND_PICK_AFTER covered
    `transfer_failed` the attempt ended right there and the track kept the 320 kbps Deezer file, with
    five good survivors never tried."""
    settings, store, _, provider, _, _ = lenv
    provider.files = [lf("rejects"), lf("sends")]
    provider.audio["sends"] = provider.audio["a"]
    original = provider.download

    async def reject_the_first(file, **kw):
        if file.username == "rejects":
            provider.downloaded.append(file.username)
            raise LosslessError("transfer ended Completed, Rejected", "transfer_failed")
        return await original(file, **kw)

    provider.download = reject_the_first
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    a = attempt_of(store, rid)
    assert a.outcome == "filed" and provider.downloaded == ["rejects", "sends"]
    assert store.get_track(r.track_id).source == "soulseek"
    assert not list(settings.tmp_dir.iterdir())


async def test_verify_failure_tries_the_second_pick(lenv, tmp_path: Path):
    settings, store, _, provider, _, _ = lenv
    provider.files = [lf("a"), lf("b", queue_length=1)]
    provider.audio["a"] = _fake_flac(tmp_path / "fake.flac")
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    a = attempt_of(store, rid)
    assert a.outcome == "filed" and provider.downloaded == ["a", "b"]
    assert [e["detail"]["pick"] for e in a.timeline if e["event"] == "enqueue"] == [1, 2]
    assert store.get_track(r.track_id).source == "soulseek" and a.spectrogram_path
    assert not list(settings.tmp_dir.iterdir())


async def test_verify_failure_on_every_pick_falls_back_without_a_rejection_row(lenv, tmp_path: Path):
    settings, store, _, provider, _, _ = lenv
    fake = _fake_flac(tmp_path / "fake.flac")
    provider.files = [lf("a"), lf("b"), lf("c")]
    provider.audio = {"a": fake, "b": fake, "c": fake}
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and store.get_track(r.track_id).source == "deezer_bot"
    a = attempt_of(store, rid)
    assert a.outcome == "verify_failed" and provider.downloaded == ["a", "b", "c"]  # every survivor, under the cap
    assert store.get_rejection_for_request(rid) is None
    assert not list(settings.tmp_dir.iterdir())


async def test_second_pick_needs_budget(lenv, tmp_path: Path):
    _, store, _, provider, _, clock = lenv
    provider.files = [lf("a"), lf("b")]
    provider.audio["a"] = _fake_flac(tmp_path / "fake.flac")
    original = provider.download

    async def slow(file, **kw):
        clock.t += 28                                         # 35 s budget - 10 s first-byte cap = 25 s: gone
        return await original(file, **kw)

    provider.download = slow
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    await w.process(rid)
    a = attempt_of(store, rid)
    assert a.outcome == "verify_failed" and provider.downloaded == ["a"]
    assert any(e["event"] == "budget_exhausted" for e in a.timeline)


async def test_fingerprint_failure_falls_back_and_keeps_the_fingerprints(lenv):
    settings, store, _, _, fake_check, _ = lenv
    fake_check.result = FingerprintResult("failed", 0.61, 40.0, "best score 0.61 below 0.90", [9], [8])
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    a = attempt_of(store, rid)
    assert a.outcome == "fingerprint_failed" and a.fingerprint["score"] == 0.61
    assert (Path(a.raw_dir) / "04-fingerprint.json").read_text() == '{"preview": [9], "track": [8]}'
    assert store.get_track(r.track_id).source == "deezer_bot" and not list(settings.tmp_dir.iterdir())


async def test_fingerprint_skipped_still_files_and_says_so(lenv):
    _, store, _, _, fake_check, _ = lenv
    fake_check.result = FingerprintResult("skipped", None, None, "fpcalc not installed")
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert attempt_of(store, rid).outcome == "filed"
    kinds = {e.kind: e.value for e in store.list_evidence(r.track_id)}
    assert kinds["recording_match"] == {"status": "skipped", "score": None, "offset_s": None,
                                        "reference": "deezer:1754956977", "reason": "fpcalc not installed"}
    assert "fingerprint" not in kinds


async def test_convert_failure_falls_back(lenv, monkeypatch):
    settings, store, _, _, _, _ = lenv

    def boom(src, fmt, bit_depth):
        raise ConvertError("ffmpeg exploded")

    monkeypatch.setattr(worker_mod, "to_format", boom)
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert attempt_of(store, rid).outcome == "convert_failed" and store.get_track(r.track_id).source == "deezer_bot"
    assert not list(settings.tmp_dir.iterdir())


async def test_flac_filing_format_keeps_the_file(lenv):
    settings, store, notifier, _, _, _ = lenv
    settings.lossless_filing_format = "flac"
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    t = store.get_track(r.track_id)
    assert t.path.suffix == ".flac" and (t.fmt, t.source_fmt) == ("flac", "flac")
    assert "FLAC 16-bit/44.1 kHz via Soulseek" in notifier.sent[-1][0]
    assert not list(settings.tmp_dir.iterdir())


async def test_miss_then_deezer_failure_runs_no_second_attempt(lenv):
    _, store, _, provider, _, _ = lenv
    provider.files = [lf("a", extension="mp3")]
    source = FakeSource([good_cand()], fetch_error=SourceTimeout("slow"))
    w = make(lenv, source=source)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 1 and r.fetch_source is None
    store.update_request(rid, retry_after=None)
    r = await w.process(rid)
    assert r.attempts == 2 and len(provider.searches) == 1          # the miss is remembered; no second attempt
    assert len(store.list_attempts()) == 1 and len(store.get_candidates(rid)) == 1


async def test_unexpected_error_inside_the_attempt_is_recorded(lenv):
    _, store, _, provider, _, _ = lenv
    provider.search_error = RuntimeError("bug")
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    a = attempt_of(store, rid)
    assert r.state == RequestState.DONE and a.outcome == "transfer_failed"
    assert any(e["event"] == "error" and "bug" in e["detail"]["message"] for e in a.timeline)


async def test_recorder_or_request_update_failure_before_the_attempt_still_falls_back(lenv):
    """A raise between constructing the AttemptRecorder and starting the attempt (e.g. a locked sqlite
    write) must not escape into process() as ERROR, and must not leave the attempt row's outcome NULL --
    both were possible while `AttemptRecorder(...)` and the fetch_source update sat outside the
    per-provider try."""
    _, store, notifier, provider, _, _ = lenv
    real_update_request = store.update_request
    raised = []

    def flaky_update_request(request_id, **fields):
        if not raised and fields.get("fetch_source") == "soulseek":
            raised.append(True)
            raise RuntimeError("database is locked")
        return real_update_request(request_id, **fields)

    store.update_request = flaky_update_request
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert raised == [True]                        # the injected failure actually fired
    assert r.state == RequestState.DONE             # not ERROR: it fell back instead of escaping
    assert provider.searches == []                  # failed before the attempt ever searched
    t = store.get_track(r.track_id)
    assert t.source == "deezer_bot"                 # filed the Deezer fallback, not a lossless hit
    a = attempt_of(store, rid)
    assert a.outcome == "transfer_failed"           # closed, not left NULL
    assert any(e["event"] == "error" and "database is locked" in e["detail"]["message"] for e in a.timeline)
    assert "MP3 320 kbps via Deezer" in notifier.sent[-1][0]


async def test_recovery_write_failure_inside_the_except_handler_still_falls_back(lenv):
    """Finding 2 (scoped re-review): rec.event/rec.finish inside _try_lossless's except handler
    write to the store too. If that recovery write also fails, the failure must not escape either --
    the attempt row may be left with a NULL outcome (sqlite is refusing writes; nothing more the
    process can do), but the request must still fall back to Deezer instead of RequestState.ERROR."""
    _, store, notifier, provider, _, _ = lenv

    def always_raise(*args, **kwargs):
        raise RuntimeError("database is locked")

    store.update_attempt = always_raise                # every store.update_attempt call fails from here on
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert provider.searches == []                      # failed before search: rec.event("search_started") raised
    assert r.state == RequestState.DONE                 # not ERROR: the recovery write's own failure was swallowed
    t = store.get_track(r.track_id)
    assert t.source == "deezer_bot"                     # fell back to Deezer instead of erroring out
    assert "MP3 320 kbps via Deezer" in notifier.sent[-1][0]


async def test_finish_write_failure_after_a_hit_does_not_orphan_the_converted_file(lenv):
    """Found by the final whole-branch review: rec.finish("filed") in _attempt writes to the store. If
    that write fails, the exception unwinds to _try_lossless's except handler, which only has `rec` in
    scope -- it never sees the LosslessHit, so the converted file in tmp_dir must be unlinked at the
    point in _attempt that still holds it, before the failure is re-raised (spec §13: every outcome
    leaves tmp_dir empty)."""
    settings, store, _, _, _, _ = lenv
    real_update_attempt = store.update_attempt
    raised = []

    def flaky_update_attempt(attempt_id, **fields):
        if not raised and fields.get("outcome") == "filed":
            raised.append(True)
            raise RuntimeError("database is locked")
        return real_update_attempt(attempt_id, **fields)

    store.update_attempt = flaky_update_attempt
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert raised == [True]                              # the injected failure actually fired
    assert r.state == RequestState.DONE                  # fell back to Deezer instead of erroring out
    t = store.get_track(r.track_id)
    assert t.source == "deezer_bot"                       # filed the Deezer fallback, not the lossless hit
    assert list(settings.tmp_dir.iterdir()) == []          # the converted file must not be orphaned


async def test_startup_marks_open_attempts_interrupted_cancels_and_rescans(lenv):
    _, store, _, provider, _, _ = lenv
    rid = store.add_request(TEXT, RequestKind.TEXT)
    open_id = store.add_attempt(rid, "soulseek", "q")
    w = make(lenv)
    await w.startup()
    assert store.get_attempt(open_id).outcome == "interrupted" and provider.cancelled == 1 and provider.rescans == 1
    r = await w.process(rid)                                   # interrupted does not block re-entry
    assert store.get_attempt_for_request(rid).outcome == "filed" and r.state == RequestState.DONE


async def test_maintenance_runs_once_a_day(lenv, tmp_path: Path):
    settings, _, _, provider, _, clock = lenv
    old = settings.lossless_raw_dir / "1"
    old.mkdir(parents=True)
    import os
    import time
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    w = make(lenv)
    await w._maintenance(force=True)
    assert not old.exists() and provider.rescans == 1
    await w._maintenance()
    assert provider.rescans == 1
    clock.t += 86_401
    await w._maintenance()
    assert provider.rescans == 2


async def test_lossless_is_off_without_a_key_or_providers(lenv, tmp_path: Path):
    settings, store, _, provider, _, _ = lenv
    w = make(lenv, providers=[])
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and store.list_attempts() == []
    store.get_track(r.track_id).path.unlink()            # else the second request below collides with this file
    store.delete_track(r.track_id)                       # and this row, on re-entry (spec dedup by ISRC/meta)
    settings.slskd_api_key = None
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    assert (await w.process(rid)).state == RequestState.DONE and store.list_attempts() == [] and provider.searches == []


def test_format_line():
    from krater.models import Verdict
    assert format_line(Verdict(True, "mp3", 320, 19500, "r"), "deezer_bot", None) == "MP3 320 kbps via Deezer"
    v = Verdict(True, "aiff", 1411, 22050, "r", bit_depth=16, sample_rate=44100)
    assert format_line(v, "soulseek", "flac") == "AIFF 16-bit/44.1 kHz, from FLAC via Soulseek"
    assert format_line(v, "soulseek", "aiff") == "AIFF 16-bit/44.1 kHz via Soulseek"
    assert format_line(Verdict(True, "wav", 2304, 22050, "r", bit_depth=24, sample_rate=48000), "soulseek", "flac") == (
        "WAV 24-bit/48 kHz, from FLAC via Soulseek")


async def test_a_running_transfer_publishes_its_position_and_clears_it_afterwards(lenv):
    """The row's progress bar reads status["fetch_progress"]. A bar left published after the transfer
    ends is worse than no bar: it sits full on a row that has moved on."""
    _, store, _, provider, _, _ = lenv
    seen = []
    w = make(lenv)
    original = provider.download

    async def watched(file, *, on_progress=None, **kw):
        def spy(p):
            on_progress(p)
            seen.append(dict(w.status["fetch_progress"] or {}))
        return await original(file, on_progress=spy, **kw)

    provider.download = watched
    rid = store.add_request(TEXT, RequestKind.TEXT)
    await w.process(rid)
    assert [p["pct"] for p in seen] == [0, 0, 100]           # queued, 10 of 320000 bytes, then all of it
    assert [p["bytes"] for p in seen] == [0, 10, 320_000]    # bytes, not just the rounded percentage
    assert {p["request_id"] for p in seen} == {rid}
    assert seen[-1]["peer"] == "a" and seen[-1]["size"] == 320_000
    assert w.status["fetch_progress"] is None                # cleared once the attempt is over


async def test_the_position_is_cleared_when_the_transfer_fails_too(lenv):
    _, store, _, provider, _, _ = lenv
    provider.download_error = LosslessError("boom", "transfer_timeout")
    w = make(lenv)
    await w.process(store.add_request(TEXT, RequestKind.TEXT))
    assert w.status["fetch_progress"] is None


async def test_a_lossy_fallback_says_in_the_notification_why_the_lossless_copy_was_missed(lenv):
    """The owner asked to be prompted, not only shown: a track that lands on the Deezer copy has to say
    so in the message they actually read, with the reason in their words and what to do about it."""
    _, store, notifier, provider, _, _ = lenv
    provider.files = [lf("a", extension="mp3")]      # nothing lossless on offer -> no_pick
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE
    done = notifier.sent[-1][0]
    assert "No lossless copy this time — nothing on Soulseek matched this track closely enough." in done
    # no_pick is not a retryable outcome, so the line must point at the manual action, not promise a retry
    assert "Use Try again in the app" in done and "try again on its own" not in done


async def test_an_unreachable_provider_promises_the_automatic_retry_instead_of_asking_for_a_click(lenv):
    _, store, notifier, provider, _, _ = lenv
    provider.health_status = "unreachable"
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    await w.process(rid)
    done = notifier.sent[-1][0]
    assert "Soulseek was not reachable at the time. It will try again on its own." in done


async def test_a_filed_lossless_track_says_nothing_about_a_miss(lenv):
    _, store, notifier, _, _, _ = lenv
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and attempt_of(store, rid).outcome == "filed"
    assert "No lossless copy" not in notifier.sent[-1][0]


async def test_with_the_provider_off_a_deezer_file_is_not_blamed_on_soulseek(lenv):
    """No attempt ran, so there is nothing to report - the message must not imply Soulseek let them down
    when it was never asked."""
    _, store, notifier, _, _, _ = lenv
    # lossless_enabled is derived from the api key, so configure the worker with no provider instead
    w = make(lenv, providers=[])
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and store.get_attempt_for_request(rid) is None
    assert "No lossless copy" not in notifier.sent[-1][0]


def test_miss_reasons_match_the_ui():
    """The row badge in web/src/presentation.ts renders the same sentences. Two copies in two languages
    drift silently, so pin them here rather than plumb a static string table through the API."""
    from krater.models import ATTEMPT_OUTCOMES, MISS_REASON
    ts = (Path(__file__).resolve().parents[1] / "web" / "src" / "presentation.ts").read_text()
    assert set(MISS_REASON) == set(ATTEMPT_OUTCOMES) - {"filed"}, "every non-filed outcome needs a reason"
    for outcome, reason in MISS_REASON.items():
        assert f"{outcome}: '{reason}'" in ts, f"presentation.ts disagrees about {outcome}"


async def test_every_progress_tick_is_pushed_to_the_browser_not_just_stored(lenv):
    """The bar is driven by SSE, not by polling, so setting the dict is only half the path. `Status` is a
    dict subclass that publishes on __setitem__ -- if the worker is ever handed a plain dict (or a Status
    with no bus) the tests above still pass while the bar sits frozen at 0% for the whole download."""
    from krater.events import EventBus, Status
    _, store, _, _, _, _ = lenv
    bus = EventBus()
    w = make(lenv, status=Status(bus, telegram_authorized=True))
    q = bus.subscribe()
    await w.process(store.add_request(TEXT, RequestKind.TEXT))
    frames = []
    while not q.empty():
        name, data = q.get_nowait()
        if name == "status" and "fetch_progress" in data:
            frames.append(data["fetch_progress"])
    moving = [f for f in frames if f is not None]
    assert [f["pct"] for f in moving] == [0, 0, 100]
    assert frames[-1] is None, "the clear has to reach the page too, or the bar stays full on a done row"


# ---- upgrading a track that was filed on the lossy copy (the L.S.D. case) --------------------------
async def _file_on_deezer(lenv, provider):
    """Drive a request to DONE with the lossless attempt missing, so it lands on the Deezer mp3."""
    _, store, _, _, _, _ = lenv
    provider.files = [lf("a", extension="mp3")]          # nothing lossless on offer -> no_pick
    w = make(lenv, provider=provider)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE
    t = store.get_track(r.track_id)
    assert t.source_fmt is None and t.path.suffix == ".mp3"
    return w, t


async def test_upgrade_swaps_the_file_and_keeps_the_row_id(lenv):
    settings, store, notifier, provider, _, _ = lenv
    w, before = await _file_on_deezer(lenv, provider)
    provider.files = [lf("a")]                            # the flac is on offer now

    msg = await w.upgrade(before.id)

    after = store.get_track(before.id)                    # same id: evidence and playlists follow it
    assert after.source_fmt == "flac" and after.source == "soulseek" and after.path.suffix == ".wav"
    assert after.path.exists() and not before.path.exists(), "the lossy file is deleted, but only last"
    assert after.file_size == after.path.stat().st_size
    assert "Upgraded to WAV" in msg and "Upgraded:" in notifier.sent[-1][0]
    assert not list(settings.tmp_dir.iterdir())


async def test_upgrade_rewrites_the_playlist_that_points_at_the_old_path(lenv):
    settings, store, _, provider, _, _ = lenv
    pid = store.upsert_playlist("https://example.test/p", "Set")
    provider.files = [lf("a", extension="mp3")]
    w = make(lenv, provider=provider)
    rid = store.add_request(TEXT, RequestKind.TEXT, playlist_id=pid, playlist_position=0)
    r = await w.process(rid)
    before = store.get_track(r.track_id)
    m3u8 = next((settings.library_root / "Playlists").glob("*.m3u8"))
    assert before.path.name in m3u8.read_text()

    provider.files = [lf("a")]
    await w.upgrade(before.id)

    after = store.get_track(before.id)
    text = m3u8.read_text()
    assert after.path.name in text and before.path.name not in text
    assert store.playlist_ids_for_track(after.id) == [pid]


async def test_a_miss_on_upgrade_leaves_the_lossy_file_exactly_where_it_was(lenv):
    """The safety property: nothing is removed or re-pathed unless a replacement is already on disk."""
    _, store, _, provider, _, _ = lenv
    w, before = await _file_on_deezer(lenv, provider)
    provider.files = [lf("a", extension="mp3")]           # still nothing lossless
    size = before.path.stat().st_size

    msg = await w.upgrade(before.id)

    after = store.get_track(before.id)
    assert after.path == before.path and after.path.stat().st_size == size and after.source_fmt is None
    assert "No lossless copy this time" in msg


async def test_upgrade_replaces_the_fingerprint_evidence_rather_than_stacking_it(lenv):
    _, store, _, provider, _, _ = lenv
    w, before = await _file_on_deezer(lenv, provider)
    stale = store.add_evidence(before.id, "fingerprint", {"score": 0.1, "note": "describes the mp3"})
    provider.files = [lf("a")]

    await w.upgrade(before.id)

    ids = [e.id for e in store.list_evidence(before.id)]
    assert ids, "the new attempt records its own evidence"
    assert stale not in ids, "evidence for a file that no longer exists must not survive the swap"


async def test_upgrade_keeps_the_lossy_file_when_filing_the_new_one_fails(lenv, monkeypatch):
    """The ordering *is* the safety argument: the old file is unlinked last, so a failure anywhere before
    that leaves the library with a playable file and a row that still points at it. Asserting the happy-path
    end state cannot see this - both orders end up looking the same."""
    _, store, _, provider, _, _ = lenv
    w, before = await _file_on_deezer(lenv, provider)
    provider.files = [lf("a")]

    def boom(src, dest):
        raise OSError("no space left on device")

    monkeypatch.setattr(worker_mod, "file_track", boom)
    with pytest.raises(OSError, match="no space left"):
        await w.upgrade(before.id)

    after = store.get_track(before.id)
    assert before.path.exists(), "the only copy of this track was deleted before its replacement landed"
    assert after.path == before.path and after.source_fmt is None


async def test_upgrade_refuses_a_track_that_is_already_lossless(lenv):
    _, store, _, _, _, _ = lenv
    w = make(lenv)
    r = await w.process(store.add_request(TEXT, RequestKind.TEXT))
    assert store.get_track(r.track_id).source_fmt == "flac"
    with pytest.raises(ValueError, match="already the lossless copy"):
        await w.upgrade(r.track_id)


async def test_upgrade_refuses_when_the_provider_is_off(lenv):
    _, _, _, provider, _, _ = lenv
    w, before = await _file_on_deezer(lenv, provider)
    w.providers = []
    with pytest.raises(ValueError, match="Soulseek is off"):
        await w.upgrade(before.id)


async def test_upgrade_refuses_when_the_request_it_came_from_was_removed(lenv):
    _, store, _, provider, _, _ = lenv
    w, before = await _file_on_deezer(lenv, provider)
    store.delete_request(before.request_id)
    with pytest.raises(ValueError, match="has been removed"):
        await w.upgrade(before.id)


async def test_upgrade_runs_even_though_a_normal_retry_would_be_refused(lenv):
    """The whole point. `_lossless_allowed` blocks a second attempt after no_pick/transfer_failed -- the
    L.S.D. outcome -- so going through the ordinary path would silently do nothing."""
    _, store, _, provider, _, _ = lenv
    w, before = await _file_on_deezer(lenv, provider)
    req = store.get_request(before.request_id)
    assert w._lossless_allowed(req) is False
    provider.files = [lf("a")]
    await w.upgrade(before.id)
    assert store.get_track(before.id).source_fmt == "flac"
