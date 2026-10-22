import inspect
from dataclasses import replace
import json

import pytest

from flackey.config import Settings
from flackey.lossless import (
    HARD_RULES,
    IDENTITY_RANKERS,
    MAX_FILE_BYTES,
    MAX_LENGTH_S,
    PEER_RANKERS,
    RANKERS,
    RULES,
    LosslessFile,
    PickPolicy,
    PickReport,
    Reference,
    file_title,
    pick,
    policy_from_settings,
    rank_artist,
    rank_duration,
    rank_title,
    rank_version,
    reference_for,
    search_text,
    transfer_ceiling_s,
)
from flackey.lossless import (
    extended_reference,
    file_version,
    own_extended,
    wants_extended,
)
from flackey.models import Candidate, CatalogTrack

REF = Reference(artist="Hallucinogen", title="Orphic Thrench", mix_name="Original Mix", duration_s=442, deezer_id=6025986)


def mk(**kw) -> LosslessFile:
    base = {"provider": "soulseek", "username": "peer",
            "path": "Music\\Twisted\\02. Hallucinogen - Orphic Thrench.flac",
            "extension": "flac", "size": 51_223_918, "length_s": 442, "bitrate_kbps": None, "sample_rate": 44100,
            "bit_depth": 16, "has_free_slot": True, "upload_speed_bps": 3_000_000, "queue_length": 0}
    base.update(kw)
    return LosslessFile(**base)


def rejected_by(f: LosslessFile, ref: Reference = REF, policy: PickPolicy | None = None) -> str | None:
    report = pick([f], ref, policy if policy is not None else PickPolicy())
    return report.rejections[0].rule if report.rejections else None


def test_file_title_strips_numbers_separators_artist_and_hash():
    assert file_title("02. Hallucinogen - Orphic Thrench.flac", "Hallucinogen") == ("orphic thrench", None)
    assert file_title("09-mindsphere--depth_of_consciousness-6920ae5a.flac", "Mindsphere") == ("depth of consciousness", None)
    assert file_title("Sun_Project_-_01_Space_Dwarfs.flac", "SUN Project") == ("space dwarfs", None)
    assert file_title("Astral Projection - 09 - Still Dreaming (Anything Can Happen).flac", "Astral Projection") == (
        "still dreaming anything can happen", None)
    # "(Rmx)" is not a version for identify.parse_version; the version rule catches it from the leftover tokens
    assert file_title("08 - Space Dwarfs (Space Tribe Rmx).flac", "SUN Project") == ("space dwarfs space tribe rmx", None)


def test_name_and_folder_split_backslash_paths():
    f = mk()
    assert f.name == "02. Hallucinogen - Orphic Thrench.flac" and f.folder == "Music\\Twisted"


def test_extension_has_length_and_size_rules():
    assert rejected_by(mk(extension="mp3")) == "extension"
    assert rejected_by(mk(length_s=None)) == "has_length"
    assert rejected_by(mk(size=1_000)) == "plausible_size"                       # 18 kbps: truncated
    assert rejected_by(mk(size=400_000_000)) == "plausible_size"                 # 7000 kbps: absurd
    assert rejected_by(mk(sample_rate=96000)) == "plausible_size"                # hi-res: no CDJ plays it
    assert rejected_by(mk(extension="wav", size=78_000_000)) is None             # 1411 kbps wav


def test_limits_rule_caps_size_and_length_whatever_the_bitrate():
    """No track a DJ files is a gigabyte or half an hour long; a peer advertising one would fill the disk
    and hold the worker for its whole transfer ceiling, so it is never downloaded."""
    assert rejected_by(mk(size=MAX_FILE_BYTES + 1, length_s=1700, extension="wav")) == "limits"
    assert rejected_by(mk(length_s=MAX_LENGTH_S + 1, size=60_000_000 * 4)) == "limits"
    assert rejected_by(mk(length_s=MAX_LENGTH_S, size=MAX_LENGTH_S * 150_000)) is None   # 1200 kbps flac, at the cap


def test_queue_and_banned_rules():
    assert rejected_by(mk(queue_length=5), policy=PickPolicy(max_queue_length=2)) == "queue"
    assert rejected_by(mk(username="bad"), policy=PickPolicy(banned_users=frozenset({"bad"}))) == "banned_user"


def test_nothing_about_identity_rejects_any_more():
    """The four checks that used to reject before a byte was downloaded -- length, title, version, artist --
    are rankers now (issue #69). None of these files can be proven wrong without hearing them, so none of
    them is thrown away here; the fingerprint after the download is what decides."""
    remix = mk(path="x\\0102 - Orphic Thrench Oliver Lieb remix.flac")
    other_track = mk(path="x\\03 - L.S.D.flac")
    no_artist = mk(path="x\\02. Orphic Thrench.flac")
    off_by_four = mk(length_s=446)
    for f in (remix, other_track, no_artist, off_by_four):
        assert rejected_by(f) is None


def test_rankers_prefer_slot_then_16bit_then_queue_then_speed_then_size():
    a = mk(username="a", has_free_slot=False, queue_length=0)
    b = mk(username="b", bit_depth=24, size=100_000_000)
    c = mk(username="c", queue_length=3)
    d = mk(username="d", upload_speed_bps=1_000_000)
    e = mk(username="e")
    report = pick([a, b, c, d, e], REF, PickPolicy())
    assert [f.username for f in report.survivors] == ["e", "d", "c", "b", "a"]
    assert report.chosen.username == "e"
    assert [n for n, _ in PEER_RANKERS] == ["free_slot", "bit_depth", "queue_length", "upload_speed", "size"]


def test_report_summary_and_json_round_trip():
    truncated = mk(length_s=100, size=1_000, username="l")                       # 0.08 kbps: not a flac
    report = pick([mk(), mk(extension="mp3", username="m"), truncated], REF, PickPolicy())
    assert report.seen == 3 and report.chosen.username == "peer"
    assert report.summary == "3 files: 1 extension, 1 plausible_size; chose peer (slot, q0, flac)"
    again = PickReport.from_dict(json.loads(json.dumps(report.to_dict())))
    assert again.chosen == report.chosen and again.policy == report.policy and again.reference == report.reference
    assert [(r.rule, r.file.username) for r in again.rejections] == [("extension", "m"), ("plausible_size", "l")]


def test_pick_never_chooses_a_rejected_file():
    files = [mk(username=f"u{i}", length_s=442 + (i % 7), extension="flac" if i % 3 else "mp3") for i in range(40)]
    report = pick(files, REF, PickPolicy())
    rejected = {r.file for r in report.rejections}
    assert report.chosen not in rejected and not (set(report.survivors) & rejected)
    assert len(report.survivors) + len(report.rejections) == 40
    assert pick([], REF, PickPolicy()).summary == "0 files: nothing left"


def test_reference_and_search_text():
    cat = CatalogTrack(id=1, artist="Astral Projection, Someone", title="Dreaming (Anything Can Happen)",
                       mix_name="Original Mix", label="L", genre="G", duration_ms=470_000)
    cand = Candidate(source="deezer_bot", source_ref="dz_track:8095320:send", artist="Astral Projection",
                     title="Dreaming (Anything Can Happen)", duration_s=471, deezer_id=8095320)
    ref = reference_for(cat, cand)
    assert ref == Reference("Astral Projection, Someone", "Dreaming (Anything Can Happen)", "Original Mix", 470, 8095320)
    assert search_text(ref) == "Astral Projection Dreaming"
    remix = Reference("Goasia", "Love & Peace", "Filteria Remix", 500)
    assert search_text(remix) == "Goasia Love & Peace Filteria Remix"
    no_cat = reference_for(None, Candidate(source="deezer_bot", source_ref="r", artist="A", title="T (Club Edit)",
                                           duration_s=3, deezer_id=9))
    assert no_cat == Reference("A", "T", "Club Edit", 3, 9)


def test_policy_from_settings_carries_only_what_is_left(tmp_path):
    p = policy_from_settings(Settings(_env_file=None, data_dir=tmp_path, lossless_max_queue=3))
    assert p == PickPolicy(max_queue_length=3)


def test_both_known_lengths_rank_equally_and_the_gap_between_them_does_not():
    """Beatport can match a compilation master while the owner asked for the album cut, so a recording is
    known by two lengths. `rank_duration` measures against the nearer of them, which keeps both releases at
    the front without turning the span between them into one wide window."""
    ref = Reference(artist="Filteria", title="Dog Days Bliss", mix_name="Original Mix", duration_s=544,
                    requested_duration_s=532)
    album = mk(path="Suntrip\\Lost In The Wild\\02 - Filteria - Dog Days Bliss.flac", length_s=531,
               size=531 * 900 * 1000 // 8)
    compilation = mk(path="Ovnimoon\\Jikukan 2\\Filteria - Dog Days Bliss.flac", length_s=544,
                     size=544 * 900 * 1000 // 8)
    between = mk(length_s=538, size=538 * 900 * 1000 // 8)
    other = mk(path="VA\\Filteria - Dog Days Bliss (1997 Mix).flac", length_s=505, size=505 * 900 * 1000 // 8)
    assert rank_duration(album, ref) == rank_duration(compilation, ref) == 0
    assert rank_duration(between, ref) == 1 and rank_duration(other, ref) == 5
    for f in (album, compilation, between, other):
        assert rejected_by(f, ref) is None


def test_the_transfer_ceiling_grows_with_the_file_so_a_long_flac_is_not_cut_at_ninety_percent(tmp_path):
    """One flat cap cannot serve a 5 MB single and a 67 MB ten-minute FLAC. 600 s at a peer's honest
    100 kB/s files the first and cancels the second with 7 MB to go -- which is what happened to Filteria's
    "Dog Days Bliss". A floor on the rate is the thing that actually has to hold: below it the peer is
    trickling and the request is better off with the next survivor."""
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert transfer_ceiling_s(5_000_000, s) == s.lossless_transfer_s          # small file keeps the floor
    big = transfer_ceiling_s(67_085_339, s)
    assert big / 60 > 55 and big == pytest.approx(67_085_339 * 8 / (s.lossless_min_rate_kbps * 1000))
    assert transfer_ceiling_s(67_085_339, s) > transfer_ceiling_s(40_000_000, s)


def test_identity_rankers_order_survivors_instead_of_rejecting_them():
    far = mk(username="far", length_s=600)
    near = mk(username="near", length_s=444)                  # 2 s off: same bucket as exact
    exact_no_slot = mk(username="exact", length_s=442, has_free_slot=False)
    remix = mk(username="rmx", path="x\\Hallucinogen - Orphic Thrench (Twisted Remix).flac", length_s=442)
    report = pick([far, remix, exact_no_slot, near], REF, PickPolicy())     # no rules=/rankers=: the defaults
    assert not report.rejections
    assert [f.username for f in report.survivors] == ["near", "exact", "far", "rmx"]


def test_the_default_gate_is_the_hard_rules_then_the_identity_rankers():
    """Bound as `pick`'s defaults, not merely named at module level: rebinding `RULES`/`RANKERS` below the
    `def` would leave the live gate on the old lists while every test that passes them explicitly stayed
    green. `version` leads the title-closeness rankers by the replay measurement (2026-09-21-no-pick-replay.md);
    `same_song` goes before it so a version match can never lift a different song over the asked one."""
    # `cut` rejects only on the extended spelling (issue #187); for any other reference it passes everything
    assert [n for n, _ in RULES] == ["extension", "has_length", "limits", "plausible_size", "queue",
                                    "banned_user", "cut"]
    assert [n for n, _ in RANKERS][:5] == ["same_song", "version", "duration", "title", "artist"]
    bound = inspect.signature(pick).parameters
    assert bound["rules"].default is RULES and bound["rankers"].default is RANKERS
    assert RULES == HARD_RULES and RANKERS == IDENTITY_RANKERS + PEER_RANKERS


def test_a_far_off_length_survives_and_ranks_last():
    far = mk(username="far", length_s=1479, size=1479 * 900 * 1000 // 8)
    near = mk(username="near", length_s=442)
    report = pick([far, near], REF, PickPolicy())
    assert not report.rejections and [f.username for f in report.survivors] == ["near", "far"]


def test_rankers_measure_against_the_reference():
    assert rank_version(mk(), REF) == 0
    assert rank_version(mk(path="x\\Hallucinogen - Orphic Thrench (Live).flac"), REF) == 1
    assert rank_version(mk(path="x\\08 - Orphic Thrench (Space Tribe Rmx).flac"), REF) == 1
    remix = Reference("Hallucinogen", "Orphic Thrench", "Oliver Lieb Remix", 442)
    assert rank_version(mk(path="x\\0102 - Orphic Thrench Oliver Lieb remix.flac"), remix) == 0
    assert rank_version(mk(), remix) == 1                      # the original, when a remix was asked for
    assert rank_duration(mk(length_s=442), REF) == 0 and rank_duration(mk(length_s=449), REF) == 1
    assert rank_duration(mk(length_s=None), REF) == 10_000
    assert rank_title(mk(), REF) == -10 and rank_title(mk(path="x\\Someone - Else.flac"), REF) > -5
    dreaming = Reference("Astral Projection", "Dreaming (Anything Can Happen)", "Original Mix", 470)
    assert rank_title(mk(path="x\\01-09. Still Dreaming (Anything Can Happen).flac"), dreaming) == -10
    assert rank_title(mk(path="x\\08 - Still Dreaming.flac"), dreaming) > -9
    assert rank_artist(mk(), REF) == 0 and rank_artist(mk(path="x\\02. Orphic Thrench.flac"), REF) == 1


def test_pick_policy_from_dict_ignores_fields_older_reports_carry():
    d = {"lossless_extensions": ["flac"], "duration_tolerance_s": 3, "title_ratio": 90, "require_artist": False,
         "max_queue_length": None, "banned_users": [], "some_future_field": 1}
    p = PickPolicy.from_dict(d)
    assert p.lossless_extensions == frozenset({"flac"}) and p.max_queue_length is None


def test_the_right_song_from_an_idle_peer_beats_other_songs_that_name_the_version():
    """2026-10-14, with Beatport blocked: "Alpha Centauri (Remastered 2024)" was asked by its own words, so the
    reference's version was "Remastered 2024". One peer shared the whole remastered album with a queue of 222;
    another had Alpha Centauri "(Remastered)" with a free slot. Version ranked before title, so all fifteen
    other songs from the busy peer came before the right song, and every retry waited out the same queue."""
    ref = Reference(artist="Hallucinogen", title="Alpha Centauri", mix_name="Remastered 2024", duration_s=617,
                    deezer_id=None, requested_duration_s=617)
    busy = {"username": "NuagistTurtleize", "queue_length": 222}
    files = [mk(path="x\\03-hallucinogen-alpha_centauri_(remastered_2024).flac", length_s=616, **busy),
             mk(path="x\\05-hallucinogen-shamanix_(remastered_2024).flac", length_s=599, **busy),
             mk(path="x\\14-hallucinogen-gamma_goblins_pt_2_(remastered_2024).flac", length_s=527, **busy),
             mk(path="x\\03. Hallucinogen - Alpha Centauri (Remastered).flac", length_s=616, username="Ben_DeRoy")]
    survivors = pick(files, ref, PickPolicy()).survivors
    assert all("alpha" in f.name.lower() for f in survivors[:2])


LSD_2024 = Reference(artist="Hallucinogen", title="LSD", mix_name="Remastered 2024", duration_s=403,
                     requested_duration_s=403)


def test_the_year_is_left_out_of_the_search_for_a_version():
    """2026-10-20: "Hallucinogen LSD Remastered 2024" found 15 files and none was the remaster, because peers
    file it under "The Albums Remastered (2023)". Without the year the same network answered 127."""
    assert search_text(LSD_2024) == "Hallucinogen LSD Remastered"
    assert search_text(Reference("A", "T", "Remastered 2012", 300)) == "A T Remastered"


def test_a_year_that_names_a_version_stays_in_the_search_and_the_name_check():
    """Only a remaster's year is the label's: "2020 Remix" and "Club Mix 2000" are those versions' names."""
    assert search_text(Reference("A", "T", "2012 Remix", 300)) == "A T 2012 Remix"
    assert search_text(Reference("A", "T", "Club Mix 2000", 300)) == "A T Club Mix 2000"
    ref = Reference("A", "T", "2012 Remix", 300)
    assert rank_version(mk(path="x\\A - T (2012 Remix).flac", length_s=300), ref) == 0
    assert rank_version(mk(path="x\\A - T (1998 Remix).flac", length_s=300), ref) == 1     # another remix: tried after the named one


def test_a_remaster_path_ranks_first_and_a_plain_one_is_still_tried():
    """The fingerprint cannot tell a remaster from the original it was made from (both 0.96 against the
    2024 video, 2026-10-20), so the peer's path saying remaster is the first evidence. Until issue #172 a
    path without the word was rejected outright; it is now tried after every path that has it, and its own
    tags decide whether it is filed as the remaster or flagged."""
    plain = mk(path="Twisted\\01. Hallucinogen - LSD.flac", length_s=403)
    assert rejected_by(plain, LSD_2024) is None
    assert rank_version(mk(path="The Albums Remastered (2023)\\01. LSD.flac", length_s=402), LSD_2024) == 0
    assert rank_version(mk(path="x\\01-hallucinogen-lsd_(remastered_2024).flac", length_s=403), LSD_2024) == 0
    assert rank_version(plain, LSD_2024) == 1
    assert rank_version(mk(path="The Singles Remastered (2024)\\02 LSD (Live Mix).flac"), LSD_2024) == 2


def test_the_remaster_beats_a_live_mix_from_the_remastered_singles():
    """What request #384 was offered: the remastered singles' 9:09 Live Mix came first four times. The
    plain "Twisted" copy is tried before it now: it is the right arrangement, and may be the remaster."""
    files = [mk(username="live", path="The Singles Remastered (2024)\\02 LSD (Live Mix).flac", length_s=549),
             mk(username="orig", path="Twisted\\01. Hallucinogen - LSD.flac", length_s=403),
             mk(username="rm", path="The Albums Remastered (2023)\\01. LSD.flac", length_s=402)]
    report = pick(files, LSD_2024, PickPolicy())
    assert [f.username for f in report.survivors] == ["rm", "orig", "live"]


def test_an_original_request_ranks_a_remaster_path_last():
    """Asked for the original, a remastered copy would pass the fingerprint as it: it is the last thing tried,
    after even a file that names another mix (issue #172)."""
    lsd = Reference(artist="Hallucinogen", title="LSD", mix_name="Original Mix", duration_s=403)
    files = [mk(username="rm", path="The Albums Remastered (2023)\\01. LSD.flac", length_s=403),
             mk(username="rm2", path="x\\01. Hallucinogen - LSD (Remastered 2024).flac", length_s=403),
             mk(username="dub", path="x\\01. Hallucinogen - LSD (Dub).flac", length_s=403),
             mk(username="orig", path="Twisted\\01. Hallucinogen - LSD.flac", length_s=403, has_free_slot=False)]
    report = pick(files, lsd, PickPolicy())
    assert [f.username for f in report.survivors][:2] == ["orig", "dub"]
    assert {f.username for f in report.survivors[2:]} == {"rm", "rm2"}


def test_a_record_on_a_remastered_release_is_searched_and_ranked_as_the_remaster():
    """Beatport files some remasters as "Original Mix" on a release that says "Remastered": the reference
    must still ask for the remaster, or the gate would rank the remaster last (issue #172)."""
    record = CatalogTrack(id=8, isrc="USA2P0300457", artist="Hallucinogen", title="LSD", mix_name="Original Mix",
                          label="Twisted", genre="Psy-Trance", release_name="The Albums Remastered", duration_ms=403000)
    beatport = Candidate(source="beatport", source_ref="beatport:8", artist="Hallucinogen", title="LSD", duration_s=403)
    ref = reference_for(record, beatport, asked="Remastered 2024")
    assert ref.mix_name == "Remastered" and search_text(ref) == "Hallucinogen LSD Remastered"
    # a request that names no version means the track: classic catalogue sits on remastered releases
    assert reference_for(record, beatport).mix_name == "Original Mix"
    files = [mk(username="orig", path="Twisted\\01. Hallucinogen - LSD.flac", length_s=403),
             mk(username="rm", path="The Albums Remastered (2023)\\01. LSD.flac", length_s=403, has_free_slot=False)]
    assert pick(files, ref, PickPolicy()).survivors[0].username == "rm"


def test_a_deezer_candidate_on_a_remastered_album_is_the_remaster_reference():
    cand = Candidate(source="deezer_bot", source_ref="dz:2", artist="Hallucinogen", title="LSD", duration_s=403,
                     deezer_id=2, album="The Albums Remastered")
    assert reference_for(None, cand, asked="Remastered 2024").mix_name == "Remastered"
    assert reference_for(None, cand).mix_name == "Original Mix"
    assert reference_for(None, cand, asked="Extended Mix").mix_name == "Original Mix"


# Issue #187: "Prefer extended mixes". Off, nothing here changes: a reference has no cut and prefers nothing.
RADIO = Reference(artist="Calvin Harris", title="Feel So Close", mix_name="Original Mix", duration_s=206)


def ext(name: str, length_s: int = 330, **kw) -> LosslessFile:
    return mk(path=f"Music\\Calvin Harris\\18 Months\\{name}", length_s=length_s,
              size=length_s * 1000 * 1000 // 8, **kw)


def test_the_extended_spelling_searches_with_the_word_extended_alone():
    assert search_text(extended_reference(RADIO)) == "Calvin Harris Feel So Close extended"
    radio_edit = Reference(artist="Calvin Harris", title="Feel So Close", mix_name="Radio Edit", duration_s=206)
    assert search_text(extended_reference(radio_edit)) == "Calvin Harris Feel So Close extended"
    remix = Reference(artist="CamelPhat", title="Cola", mix_name="Made by Pete Remix", duration_s=200)
    assert search_text(extended_reference(remix)) == "CamelPhat Cola Made by Pete Remix extended"
    assert search_text(RADIO) == "Calvin Harris Feel So Close"


@pytest.mark.parametrize("name, own", [
    ("01-02 - Feel So Close (extended mix).flac", True),
    ("Calvin Harris - Feel So Close (Extended).flac", True),
    ("Feel So Close (Club Mix).flac", True),
    ("Feel So Close (12'' Version).flac", True),
    ("Feel So Close (Extended Mix) (feat. Nobody).flac", True),
    ("Feel So Close.flac", False),
    ("Feel So Close (Radio Edit).flac", False),
    ("Feel So Close (Liquid Soul Extended Remix).flac", False),
    ("Feel So Close (Dillon Francis Remix).flac", False),
])
def test_an_extended_file_names_an_extended_cut_of_the_asked_arrangement(name, own):
    assert own_extended(ext(name), RADIO) is own


def test_a_file_with_no_version_text_reads_its_cut_off_the_leftover_words():
    f = mk(path="x\\02.05 - Never Gonna Give You Up (Extended Version) (Rick Astley).flac")
    ref = Reference(artist="Rick Astley", title="Never Gonna Give You Up", mix_name=None, duration_s=213)
    assert file_version(f, ref).cut == "extended" and own_extended(f, ref)
    titanium = mk(path="x\\01. Titanium (feat. Sia) (Extended).flac")
    assert own_extended(titanium, Reference(artist="David Guetta", title="Titanium", mix_name=None, duration_s=245))


def test_an_extended_remix_is_the_remix_s_own_extended_cut():
    remix = Reference(artist="CamelPhat", title="Cola", mix_name="Made by Pete Remix", duration_s=200)
    assert own_extended(mk(path="x\\01 - Cola (Made by Pete extended remix).flac"), remix)
    assert not own_extended(mk(path="x\\01 - Cola (Extended Mix).flac"), remix)


@pytest.mark.parametrize("mix, wants", [
    (None, True), ("Original Mix", True), ("Radio Edit", True), ("Made by Pete Remix", True),
    ("Extended Mix", False), ("Club Mix", False), ("Remastered 2024", False),
])
def test_what_a_request_for_a_version_can_be_answered_with(mix, wants):
    assert wants_extended(mix) is wants


def test_the_extended_spelling_rejects_every_file_that_is_not_an_extended_cut():
    files = [ext("Feel So Close (Extended Mix).flac", username="a"), ext("Feel So Close.flac", 206, username="b"),
             ext("Feel So Close (Liquid Soul Extended Remix).flac", username="c")]
    report = pick(files, extended_reference(RADIO), PickPolicy())
    assert [f.username for f in report.survivors] == ["a"]
    assert {r.file.username: r.rule for r in report.rejections} == {"b": "cut", "c": "cut"}


def test_preferring_extended_ranks_it_ahead_of_the_radio_edit_at_its_exact_length():
    radio = ext("Feel So Close.flac", 206, username="radio")
    extended = ext("Feel So Close (Extended Mix).flac", 330, username="extended", has_free_slot=False)
    remix = ext("Feel So Close (Liquid Soul Extended Remix).flac", 400, username="remix")
    # off: today's order, the radio edit's length first and the extended mix last
    assert [f.username for f in pick([extended, remix, radio], RADIO, PickPolicy()).survivors][0] == "radio"
    on = pick([remix, radio, extended], replace(RADIO, prefer_extended=True), PickPolicy())
    assert [f.username for f in on.survivors] == ["extended", "radio", "remix"]
    assert rank_version(extended, RADIO) == 1 and rank_version(extended, replace(RADIO, prefer_extended=True)) == -1
    assert rank_duration(extended, replace(RADIO, prefer_extended=True)) == 0


def test_a_report_round_trips_the_cut_and_older_reports_still_load():
    report = pick([ext("Feel So Close (Extended Mix).flac")], extended_reference(RADIO), PickPolicy())
    again = PickReport.from_dict(json.loads(json.dumps(report.to_dict())))
    assert again.reference.cut == "extended" and again.reference.prefer_extended
    old = report.to_dict()
    del old["reference"]["cut"], old["reference"]["prefer_extended"]
    assert PickReport.from_dict(old).reference == RADIO.__class__(**old["reference"])
    assert PickReport.from_dict(old).reference.cut == ""
