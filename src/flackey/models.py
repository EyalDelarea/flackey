from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class RequestKind(StrEnum):
    TEXT = "text"
    YT_TRACK = "yt_track"
    YT_PLAYLIST = "yt_playlist"
    SPOTIFY_TRACK = "spotify_track"
    SPOTIFY_PLAYLIST = "spotify_playlist"


class RequestState(StrEnum):
    QUEUED = "queued"
    IDENTIFYING = "identifying"
    AWAITING_REVIEW = "awaiting_review"
    FETCHING = "fetching"
    VERIFYING = "verifying"
    FILING = "filing"
    DONE = "done"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    NOT_FOUND = "not_found"
    ERROR = "error"


INFLIGHT_STATES = {
    RequestState.IDENTIFYING,
    RequestState.FETCHING,
    RequestState.VERIFYING,
    RequestState.FILING,
}
TERMINAL_STATES = {
    RequestState.DONE,
    RequestState.DUPLICATE,
    RequestState.REJECTED,
    RequestState.CANCELLED,
    RequestState.NOT_FOUND,
    RequestState.ERROR,
}
# "Terminal" here means the pipeline has stopped and the row may be deleted -- not that the state can never
# change. Three of the six below are handed back to the queue by `Worker.retry`; see RETRYABLE_STATES.
FAILED_STATES = {
    RequestState.REJECTED,
    RequestState.CANCELLED,
    RequestState.NOT_FOUND,
    RequestState.ERROR,
}
# The subset of FAILED_STATES `Worker.retry` takes back, and the reason the Failed badge and the "Retry all"
# button count differently. Defined once so every surface that offers a retry -- the worker, the row button,
# the batch route, the words the UI puts on a row -- reads the same set; `test_failed_states_match_the_ui`
# pins `web/src/presentation.ts` to all three sets so the browser copy cannot drift away from the worker.
RETRYABLE_STATES = {RequestState.ERROR, RequestState.NOT_FOUND, RequestState.CANCELLED}
# What "Retry all" sweeps: the failures the pipeline arrived at by itself. CANCELLED is retryable but not
# swept, because it is the one failure that was a decision -- a batch that re-queued every track the owner
# had deliberately stopped would undo an afternoon of stopping them with one press (issue #92). The row's
# own "Try again" is how a stopped track comes back, one deliberate press for one deliberate stop.
SWEEPABLE_STATES = RETRYABLE_STATES - {RequestState.CANCELLED}
# The rows that carry "Report this failure" (issue #165): the failures the pipeline arrived at, retryable or
# not. CANCELLED is the owner's own decision, so there is nothing about it for the developer to look into.
# `REPORTABLE` in `web/src/presentation.ts` is pinned to this by `test_reportable_states_match_the_ui`.
REPORTABLE_STATES = FAILED_STATES - {RequestState.CANCELLED}


def is_original(version: str | None) -> bool:
    return bool(re.search(r"\boriginal\b", version or "", re.IGNORECASE))


def norm(s: str | None) -> str:
    """One normaliser for matching and dedupe (spec §5.1): lowercase, dotted acronyms joined
    ("L.S.D." -> "lsd"), remaining punctuation stripped, whitespace collapsed."""
    s = re.sub(r"(?<=\w)\.(?=\w)", "", (s or "").lower())
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


# The least a Beatport record's title may agree with the request (token_set_ratio) and still name the file.
# Measured on 105 filed requests with a Beatport match: 103 score >= 90, none between 70 and 89, and the
# only two below 70 are the two files that were wrong (issue #63). Tags only: the record and the file are
# chosen by audio, not by this number.
TITLE_FLOOR = 75


SOURCE_LABELS = {"deezer_bot": "Deezer", "soulseek": "Soulseek"}


def source_label(name: str) -> str:
    """How a source is named to the owner: the provider name is an identifier, not copy."""
    return SOURCE_LABELS.get(name, name)


@dataclass
class Query:
    raw: str
    artist: str | None = None
    title: str | None = None
    version: str | None = None
    duration_s: int | None = None

    def search_text(self) -> str:
        if self.artist and self.title:
            base = f"{self.artist} {self.title}"
            # Only a real version (a remix, an edit) helps the search. Deezer titles never carry
            # "Original Mix", and the source bot answers "No results" when it is appended.
            return f"{base} {self.version}" if self.version and not is_original(self.version) else base
        return self.raw


@dataclass
class Candidate:
    source: str
    source_ref: str
    artist: str
    title: str
    mix_name: str | None = None
    duration_s: int | None = None
    deezer_id: int | None = None
    isrc: str | None = None
    rank: int = 0
    score: int | None = None
    catalog_track_id: int | None = None
    # Whether Deezer has a 30 s sample for this record, remembered from the enrichment call that fetched it
    # anyway. Three states, not two: True = there is one, False = Deezer offers none, None = never resolved
    # (a row written before the column existed, a candidate from a source that has no Deezer record, or one
    # whose enrichment call failed). The page draws no play control for False and must not treat None as
    # False -- unknown is not "no sample". Never a substitute for the preview route's own check: the signed
    # URL is resolved at play time and can still 404.
    has_preview: bool | None = None
    id: int | None = None
    request_id: int | None = None
    # The Deezer record's release date (its album's), from the same enrichment call. Only ever used to date a
    # recording's first release earlier than the Beatport release it is tagged from (`catalog.with_original_date`).
    release_date: str | None = None


@dataclass
class CatalogTrack:
    id: int
    artist: str
    title: str
    mix_name: str
    label: str
    genre: str
    isrc: str | None = None
    sub_genre: str | None = None
    catalog_number: str | None = None
    release_name: str | None = None
    release_date: str | None = None  # YYYY-MM-DD
    bpm: int | None = None
    key: str | None = None
    duration_ms: int | None = None
    artwork_url: str | None = None
    # Where the tags came from when Beatport could not supply them (issue #146): "deezer", "discogs" or
    # "deezer+discogs". None on a Beatport record, and on a stand-in no other catalogue could fill in.
    tag_source: str | None = None
    # The recording's first release, when a source already in hand dates it earlier than this release:
    # Beatport's other releases of it, the Deezer record that was downloaded, Discogs (`catalog.with_original_date`).
    # A compilation or a reissue is what Beatport often sells, and its date tagged "One More Time" 2005.
    # None when nothing earlier is known, and always on a remaster, whose own year is the point of it.
    original_date: str | None = None

    @property
    def duration_s(self) -> int | None:
        return None if self.duration_ms is None else round(self.duration_ms / 1000)

    @property
    def tag_date(self) -> str | None:
        """The date written into the file and shown as the year: the first release's where one is known."""
        return self.original_date or self.release_date

    @property
    def year(self) -> str | None:
        return self.tag_date[:4] if self.tag_date else None

    @property
    def display_title(self) -> str:
        if re.search(r"\boriginal\b", self.mix_name, re.IGNORECASE):
            return self.title
        return f"{self.title} ({self.mix_name})"


@dataclass
class Verdict:
    passed: bool
    fmt: str
    bitrate_kbps: int
    cutoff_hz: int
    reason: str
    spectrogram_path: Path | None = None
    bit_depth: int | None = None      # lossless only; None for mp3
    sample_rate: int | None = None


# What `verify.spectral_cutoff_hz` answers when it finds no edge: Nyquist of its 44.1 kHz analysis. It is the
# absence of a measurement, not one -- silence produces it too -- so it is never printed as a frequency
# ("verified to 22.1 kHz" claimed content up there that nobody had seen, issue #97).
NO_EDGE_HZ = 22_050


def edge_text(cutoff_hz: int, measured: str = "content to") -> str:
    """The spectral check's finding in words: where the content stops (`measured` names it: "content to"
    in a sentence, "cutoff" in a file's comment tag), or that no edge was found."""
    return "no edge found" if cutoff_hz >= NO_EDGE_HZ else f"{measured} {cutoff_hz / 1000:.1f} kHz"


@dataclass
class Track:
    id: int
    path: Path
    fmt: str
    bitrate_kbps: int
    cutoff_hz: int
    file_size: int
    artist: str
    title: str
    mix_name: str
    duration_s: int | None
    isrc: str | None
    catalog_track_id: int | None
    request_id: int | None
    added_at: str
    verified_at: str | None = None          # spec §4.2; same instant as added_at in v1
    spectrogram_path: Path | None = None    # spec §6: the PNG is kept for passed tracks too
    source: str = "deezer_bot"              # provider name; "soulseek" for a lossless upgrade
    source_fmt: str | None = None           # format as downloaded ("flac") when it differs from fmt
    bit_depth: int | None = None
    sample_rate: int | None = None


@dataclass
class Request:
    id: int
    created_at: str
    updated_at: str
    raw_text: str
    kind: RequestKind
    state: RequestState
    playlist_id: int | None = None
    playlist_position: int | None = None
    source_url: str | None = None
    query_artist: str | None = None
    query_title: str | None = None
    query_version: str | None = None
    query_duration_s: int | None = None
    chosen_candidate_id: int | None = None
    catalog_track_id: int | None = None
    confidence: int | None = None
    flag_reason: str | None = None
    error_message: str | None = None
    attempts: int = 0
    retry_after: str | None = None  # ISO timestamp; `due_queued` skips the request until then (backoff)
    track_id: int | None = None
    fetch_source: str | None = None         # "soulseek" or "deezer" while FETCHING; cleared after
    # Which stage the request was in when it landed in `error`, stamped by `Worker._set_state` and
    # cleared by every path that puts the row back on the queue. `error` is the only failure state that
    # needs it: not_found, rejected and cancelled each name their own stage. None on a row that has never
    # failed, and on every row written before the column existed.
    failed_stage: str | None = None
    # One manual pass at the lossless providers, granted by `Worker.retry` on a failed request and spent by
    # the next fetch. Spec §5 bars the *worker* from going back after a definitive miss; this is the owner
    # asking, which `_no_route`'s advice tells them to do.
    lossless_retry: int = 0
    # Set once, the first time the request is parked for the owner to choose, and never cleared. Every other
    # field that marks the pause is gone by the time they have picked: `flag_reason` is cleared by `choose`,
    # and `chosen_candidate_id` is written by the auto-pick as well. Without this the progress ladder cannot
    # tell a track that waited on a person from one that never did.
    reviewed: int = 0

    def query(self) -> Query:
        return Query(
            raw=self.raw_text,
            artist=self.query_artist,
            title=self.query_title,
            version=self.query_version,
            duration_s=self.query_duration_s,
        )


@dataclass
class Playlist:
    id: int
    source_url: str
    name: str
    created_at: str
    updated_at: str
    track_ids: list[int] = field(default_factory=list)
    track_positions: list[int] = field(default_factory=list)


@dataclass
class Rejection:
    id: int
    request_id: int
    reason: str
    bitrate_kbps: int | None
    cutoff_hz: int | None
    spectrogram_path: str | None
    created_at: str
    # Which check the file failed, for a page that has to explain it: 'quality' or 'different_recording'.
    # Defaulted so a row written before the column existed still loads, and reads as the only kind there
    # was then.
    kind: str = "quality"
    # The refused file, kept so the owner can hear it before deciding (issues #92, #97). Set on a different
    # recording and on a spectral quality refusal (one with a cutoff), never on one for bitrate or format,
    # and cleared the moment the file leaves -- filed or deleted.
    audio_path: str | None = None


@dataclass
class LosslessAttempt:
    id: int
    request_id: int
    provider: str
    created_at: str
    query: str
    outcome: str | None = None
    report: dict | None = None
    timeline: list = field(default_factory=list)
    fingerprint: dict | None = None
    spectrogram_path: str | None = None
    first_byte_ms: int | None = None
    total_ms: int | None = None
    raw_dir: str | None = None


ATTEMPT_OUTCOMES = ("filed", "no_pick", "queued", "first_byte_timeout", "transfer_timeout", "transfer_failed",
                    "verify_failed", "damaged", "verify_unavailable", "fingerprint_failed",
                    "fingerprint_unavailable", "convert_failed", "unavailable", "interrupted")

# Why a lossless attempt came back empty, in the owner's words rather than the vocabulary above. Every
# outcome but "filed" needs one: a track kept on the lossy copy is a thing the owner asked to be told
# about, and "transfer_failed" tells them nothing. `web/src/presentation.ts` renders the same strings for
# the row badge; `test_miss_reasons_match_the_ui` keeps the two from drifting.
MISS_REASON = {
    "no_pick": "nothing on Soulseek matched this track closely enough",
    "transfer_failed": "the people who had it would not send it",
    "first_byte_timeout": "the people who had it never started sending",
    "queued": "the people who had it are busy and their queue has not reached us yet",
    "transfer_timeout": "the people who had it stopped sending part-way through",
    # Evidence, not a verdict on anyone's honesty: a cutoff is consistent with a lossy source and can also
    # come from a resampler or a mastering filter (issue #97).
    "verify_failed": "the copies offered did not pass the lossless check",
    # A transfer problem, not a verdict on the audio: the bytes could not be read to the end (issue #97).
    "damaged": "the copies that arrived were damaged or incomplete",
    "verify_unavailable": "ffmpeg is missing, so the audio could not be checked",
    "fingerprint_failed": "the copies offered were a different recording",
    "fingerprint_unavailable": "the recording could not be checked acoustically",
    "convert_failed": "the file could not be converted",
    "unavailable": "Soulseek was not reachable at the time",
    "interrupted": "it was interrupted",
}
# `no_pick` covers two different misses: results came back and none was close enough, or the search came
# back empty. The second is not about matching at all, and "nothing matched closely enough" sent the owner
# off to doubt the match when nobody online had the track. `miss_reason` picks between them.
NO_RESULTS_REASON = "Soulseek found no copies of this track"


def results_seen(a: LosslessAttempt) -> int | None:
    """How many files this attempt's search returned: the pick report's count, or the timeline's where the
    pick never ran. None when neither recorded it -- a row from before either existed."""
    seen = (a.report or {}).get("seen")
    if seen is None:
        seen = next((e.get("detail", {}).get("files") for e in a.timeline
                     if isinstance(e, dict) and e.get("event") == "search_completed"), None)
    return seen if isinstance(seen, int) else None


def found_nothing(attempts: list[LosslessAttempt]) -> bool:
    """Whether the `no_pick` that ended these attempts (oldest first) came from searches that returned no
    files at all. Every spelling of a pass that ends in `no_pick` ended in `no_pick` itself
    (`lossless_attempt.pass_outcome`), so the pass is inside the trailing run of `no_pick` rows; the run may
    reach back into an earlier pass too, which only ever keeps the older wording. One search that came back
    with something, or one that did not say, and "found no copies" would not be the whole truth."""
    run = []
    for a in reversed(attempts):
        if a.outcome != "no_pick":
            break
        run.append(a)
    return bool(run) and all(results_seen(a) == 0 for a in run)


def miss_reason(outcome: str | None, attempts: list[LosslessAttempt] | None = None) -> str | None:
    """`MISS_REASON` for `outcome`, except a `no_pick` whose searches all came back empty, which says so."""
    if outcome == "no_pick" and attempts and found_nothing(attempts):
        return NO_RESULTS_REASON
    return MISS_REASON.get(outcome) if outcome else None


@dataclass
class Evidence:
    id: int
    track_id: int
    kind: str
    value: dict
    created_at: str
