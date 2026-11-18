"""Stand-in candidates and the small value types the pipeline passes between its stages."""
from __future__ import annotations

import zlib
from dataclasses import dataclass
from pathlib import Path

from ..fingerprint import AcousticReference, FingerprintError, FingerprintResult
from ..lossless import Reference
from ..match import candidate_version
from ..models import Candidate, CatalogTrack, Query, Verdict, source_label
from ..version import VersionCheck
from ..youtube import YouTubeError


def _mmss(seconds: int | None) -> str:
    if seconds is None:
        return "?:??"
    return f"{seconds // 60}:{seconds % 60:02d}"


CATALOG_SOURCE = "catalog"   # a candidate built from a stored record; see catalog_candidate
QUERY_SOURCE = "query"      # a candidate built from the request itself; see query_candidate
# Exactly what `fingerprint.check` used to swallow on its own, now that the fetch happens here instead:
# a missing reference is a "skipped" check, never a crashed request. fpcalc handing back unparseable JSON
# raises ValueError and a dead disk raises OSError, and neither is `upgrade()`'s "ValueError means tell the
# owner why" -- without this they would reach the API as a 409 or a 500 on a button that used to answer.
REFERENCE_ERRORS = (YouTubeError, FingerprintError, OSError, ValueError)

def catalog_candidate(catalog: CatalogTrack) -> Candidate:
    """A stand-in candidate built from a stored catalog record -- the mirror of `_fallback_catalog`, which
    builds a catalog record from a candidate. Its only use is a search spelling (`_search_references`): the
    record's artist, title and mix as the first text typed into Soulseek, for a request or a library track
    that still carries one (a Beatport record stored before Beatport was removed). Never persisted.
    """
    return Candidate(source=CATALOG_SOURCE, source_ref=f"{CATALOG_SOURCE}:{catalog.id}",
                     artist=catalog.artist, title=catalog.title, mix_name=catalog.mix_name,
                     duration_s=catalog.duration_s, isrc=catalog.isrc)


def query_candidate(query: Query, request_id: int) -> Candidate:
    """What the owner asked for, as a candidate: the parsed artist, title and version plus the video's length.
    Built when no record was chosen (issue #69). `lossless.reference_for` takes the search text from it and
    `_fallback_catalog` the tags; the only thing that vouches for the file is the fingerprint against the
    request's own audio, which is exactly the check that never depended on either catalogue. Never
    persisted: a retry rebuilds it from the row's query columns."""
    return Candidate(source=QUERY_SOURCE, source_ref=f"{QUERY_SOURCE}:{request_id}", artist=query.artist or "",
                     title=query.title or "", mix_name=query.version or "Original Mix", duration_s=query.duration_s)


def _fallback_catalog(cand: Candidate) -> CatalogTrack:
    # negative so it never collides with a stored Beatport id; crc32 (not hash()) so it is stable across processes
    fallback_id = -(cand.deezer_id or zlib.crc32(cand.source_ref.encode()) or 1)
    return CatalogTrack(id=fallback_id, isrc=cand.isrc,
                        artist=cand.artist, title=cand.title, mix_name=candidate_version(cand),
                        label="Unknown", genre="Unknown", duration_ms=(cand.duration_s or 0) * 1000 or None)


@dataclass
class Acoustic:
    """What the fingerprint compares a download against, or why there is nothing to compare against."""
    reference: AcousticReference | None
    missing: str = ""


@dataclass
class LosslessHit:
    path: Path                 # converted file in tmp_dir; to_format always rebuilds (flac included), so
                               # this is never `src` -- the only temp file left
    verdict: Verdict           # cutoff from the FLAC verify; fmt, bitrate, bit depth, sample rate re-probed after conversion
    fingerprint: FingerprintResult
    provider: str
    source_fmt: str
    attempt_id: int
    reference: Reference     # the spelling that actually found this file; `from_query` says whose words
                             # they were, which is what decides the tags when no record was chosen
    # What the file's own path and tags said about its version before conversion replaced them (issue #172);
    # None when nothing was in question
    version_check: VersionCheck | None = None
    # Which cut came, by the file's name and tags: "extended", or "" for the reference's own (issue #187)
    cut: str = ""
    cut_name: str | None = None       # the extended cut's own name, when the file gives one
    duration_s: float | None = None   # the converted file's own length, probed


def format_line(verdict: Verdict, source: str, source_fmt: str | None) -> str:
    """The end-user's view of what was downloaded (spec §4): `AIFF 16-bit/44.1 kHz, from FLAC via Soulseek`."""
    if verdict.fmt == "mp3":
        head = f"MP3 {verdict.bitrate_kbps} kbps"
    else:
        head = verdict.fmt.upper()
        if verdict.bit_depth:
            head += f" {verdict.bit_depth}-bit"
        if verdict.sample_rate:
            head += f"/{verdict.sample_rate / 1000:g} kHz"
        if source_fmt and source_fmt != verdict.fmt:
            head += f", from {source_fmt.upper()}"
    return f"{head} via {source_label(source)}"
