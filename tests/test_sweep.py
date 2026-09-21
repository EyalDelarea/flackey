from pathlib import Path

import httpx

from flackey import sweep as sweep_mod
from flackey.config import Settings
from flackey.fingerprint import AcousticReference, FingerprintResult
from flackey.models import Candidate, RequestKind
from flackey.store import Store
from flackey.sweep import SweepRow, render, run


async def test_sweep_scores_file_and_record_against_the_video_and_reuses_the_reference(tmp_path: Path, monkeypatch):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h", library_root=tmp_path / "lib",
                        data_dir=tmp_path / "data")
    store = Store(settings.db_path)
    rid = store.add_request("A - B", RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=abc")
    store.update_request(rid, query_duration_s=300)
    [cid] = [c.id for c in store.add_candidates(rid, [Candidate("deezer_bot", "dz_track:7:send", "A", "B", deezer_id=7)])]
    store.update_request(rid, chosen_candidate_id=cid)
    f = tmp_path / "lib" / "A" / "A - B.aiff"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    tid = store.add_track(path=f, fmt="aiff", bitrate_kbps=1411, cutoff_hz=20000, file_size=1, artist="A", title="B",
                          mix_name="Original Mix", duration_s=298, isrc=None, catalog_track_id=None, request_id=rid)
    fetched = []
    full = list(range(100, 400))

    async def fake_youtube(url, tmp_dir, *, duration_s=None):
        fetched.append(url)
        return AcousticReference("youtube", "abc", [full[135:376]], full, 135.0, 30.0)

    async def fake_check(path, reference, *, minimum, missing=""):
        return FingerprintResult("matched", 0.97, 12.0, "ok", reference=reference.label)

    async def fake_needles(deezer_id, http, tmp_dir):
        return [full[50:290]]                              # a slice of the video: the preview is the same recording

    monkeypatch.setattr(sweep_mod, "youtube_reference", fake_youtube)
    monkeypatch.setattr(sweep_mod, "check", fake_check)
    monkeypatch.setattr(sweep_mod, "deezer_needles", fake_needles)
    async with httpx.AsyncClient() as http:
        rows = await run(store, settings, http)
        assert rows == [SweepRow(tid, "A", "B", 298, 300, 0.97, 1.0, "matched")]
        assert store.get_reference(rid)["ref"] == "abc"
        await run(store, settings, http)
    assert fetched == ["https://www.youtube.com/watch?v=abc"]      # the second sweep reused the stored reference
    text = render(rows, 0.9)
    assert "| 0.97 | 1.00 |" in text and "A - B" in text
