from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class RequestKind(StrEnum):
    TEXT = "text"
    YT_TRACK = "yt_track"
    YT_PLAYLIST = "yt_playlist"


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


def is_original(version: str | None) -> bool:
    return bool(re.search(r"\boriginal\b", version or "", re.IGNORECASE))


def norm(s: str | None) -> str:
    """One normaliser for matching and dedupe (spec §5.1): lowercase, dotted acronyms joined
    ("L.S.D." -> "lsd"), remaining punctuation stripped, whitespace collapsed."""
    s = re.sub(r"(?<=\w)\.(?=\w)", "", (s or "").lower())
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


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
    id: int | None = None
    request_id: int | None = None


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

    @property
    def duration_s(self) -> int | None:
        return None if self.duration_ms is None else round(self.duration_ms / 1000)

    @property
    def year(self) -> str | None:
        return self.release_date[:4] if self.release_date else None

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
    # One manual pass at the lossless providers, granted by `Worker.retry` on a failed request and spent by
    # the next fetch. Spec §5 bars the *worker* from going back after a definitive miss; this is the owner
    # asking, which `_lossless_miss_line` tells them to do.
    lossless_retry: int = 0

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


@dataclass
class Rejection:
    id: int
    request_id: int
    reason: str
    bitrate_kbps: int | None
    cutoff_hz: int | None
    spectrogram_path: str | None
    created_at: str


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
                    "verify_failed", "fingerprint_failed", "convert_failed", "unavailable", "interrupted")

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
    "verify_failed": "the copies offered were not really lossless",
    "fingerprint_failed": "the copies offered were a different recording",
    "convert_failed": "the file could not be converted",
    "unavailable": "Soulseek was not reachable at the time",
    "interrupted": "it was interrupted",
}


@dataclass
class Evidence:
    id: int
    track_id: int
    kind: str
    value: dict
    created_at: str
