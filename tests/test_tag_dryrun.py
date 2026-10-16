"""`flackey tags-dry-run`: the fallback looked up for tracks Beatport did tag, side by side. Deezer and
Discogs are served from tests/fixtures/tag_lookup; nothing reaches the network."""
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from flackey.cli import app
from flackey.deezer import DeezerApi
from flackey.models import Candidate, CatalogTrack, RequestKind
from flackey.store import Store
from flackey.tag_dryrun import render, run
from flackey.tag_lookup import Discogs, TagLookup
from tests.test_cli import _env
from tests.test_tag_lookup import NoWait, serve_deezer, serve_discogs


def _library(store: Store, lib: Path) -> None:
    """Two Beatport-tagged tracks: one downloaded from a Deezer record, one with no request behind it."""
    itv = CatalogTrack(id=16552105, artist="Astral Projection", title="Into the Void", mix_name="Original Mix",
                       label="Sacred Technology", genre="Psy-Trance", sub_genre="Full-On",
                       catalog_number="STCD012", release_date="2022-06-03", isrc="UKU932231081", duration_ms=442000)
    ac = CatalogTrack(id=777, artist="Hallucinogen", title="Alpha Centauri", mix_name="Original Mix",
                      label="Twisted Records", genre="Psy-Trance", catalog_number="TWSCD4R",
                      release_date="2024-03-01", duration_ms=616000)
    store.upsert_catalog_track(itv)
    store.upsert_catalog_track(ac)
    rid = store.add_request("astral projection into the void", RequestKind.TEXT)
    [cand] = store.add_candidates(rid, [Candidate(source="deezer_bot", source_ref="dz_track:1754956977:send",
                                                  artist="Astral Projection", title="Into the Void", duration_s=442,
                                                  deezer_id=1754956977, isrc="UKU932231081")])
    store.update_request(rid, chosen_candidate_id=cand.id, catalog_track_id=itv.id)
    store.add_track(path=lib / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                    artist=itv.artist, title=itv.title, mix_name=itv.mix_name, duration_s=442, isrc=itv.isrc,
                    catalog_track_id=itv.id, request_id=rid)
    store.add_track(path=lib / "b.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                    artist=ac.artist, title=ac.title, mix_name=ac.mix_name, duration_s=616, isrc=None,
                    catalog_track_id=ac.id, request_id=None)
    # a track filed without Beatport is not something to compare against
    store.add_track(path=lib / "c.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                    artist="X", title="Y", mix_name="Original Mix", duration_s=300, isrc=None,
                    catalog_track_id=-3, request_id=None)


async def test_dry_run_compares_with_beatport(tmp_path: Path):
    store = Store(tmp_path / "db.sqlite")
    _library(store, tmp_path)
    clock = NoWait()
    with respx.mock as router:
        serve_deezer(router)
        serve_discogs(router)
        async with httpx.AsyncClient() as http:
            lookup = TagLookup(DeezerApi(http), Discogs(http, None, clock=clock.clock, sleep=clock.sleep))
            rows = await run(store, lookup)
    by_title = {r.track.title: r for r in rows}
    assert set(by_title) == {"Into the Void", "Alpha Centauri"}
    itv, ac = by_title["Into the Void"], by_title["Alpha Centauri"]
    assert itv.keyed_by == "deezer" and itv.found.sources == ["deezer", "discogs"]
    assert ac.keyed_by == "track" and ac.found.sources == ["discogs"]
    # Beatport sells the 2024 remaster; keyed by the track alone the fallback finds the 1997 original
    assert (ac.found.catalog_number, ac.found.release_date) == ("TWSCD4", "1997")

    report = render(rows)
    assert "2 Beatport-tagged tracks" in report
    assert "| Label | 2/2 | 2/2 |" in report
    assert "| Cat. no. | 2/2 | 1/2 |" in report
    assert "| Cover | 1/2 | – |" in report
    assert "**TWSCD4R → TWSCD4**" in report      # a disagreement is marked


def test_dry_run_command_writes_the_report(tmp_path: Path):
    env = _env(tmp_path)
    store = Store(tmp_path / "data" / "flackey.sqlite")
    _library(store, tmp_path / "lib")
    out = tmp_path / "report.md"
    with respx.mock as router:
        serve_deezer(router)
        serve_discogs(router)
        r = CliRunner().invoke(app, ["--env", str(env), "tags-dry-run", "--out", str(out), "--limit", "1"])
    assert r.exit_code == 0, r.output
    assert "1 tracks looked up" in r.output and out.exists()
    assert (tmp_path / "data" / "cache" / "discogs").is_dir()     # answers cached for the next run
