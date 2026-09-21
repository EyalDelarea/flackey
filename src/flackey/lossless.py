"""The gate that turns a provider's search results into one pick, with a report that explains every
rejection (spec §6). Pure: no I/O, no clock. Rules are the cheap filters that keep files that can never
satisfy the goal (lossy, implausible) from being downloaded; identity is ordered by the rankers and proven
by the fingerprint after the download."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields

from rapidfuzz import fuzz

from .config import Settings
from .identify import parse_version
from .models import Candidate, CatalogTrack, is_original, norm

LOSSLESS_EXTENSIONS = frozenset({"flac", "wav", "aiff", "aif"})
MAX_SAMPLE_RATE = 48_000                       # CDJs play 44.1 and 48 kHz; nothing higher gets downloaded
SIZE_KBPS = {"flac": (400, 2500), "wav": (1400, 4700), "aiff": (1400, 4700), "aif": (1400, 4700)}
VERSION_WORDS = frozenset({"remix", "rmx", "mix", "edit", "version", "dub", "rework", "bootleg", "mashup",
                           "live", "instrumental", "acoustic", "vip", "remixed"})
_TRACK_NO = re.compile(r"^\s*[\[(]?\d{1,3}[\])]?\s*[.\-_)]?\s*")
_TRAILING_HASH = re.compile(r"-[0-9a-f]{6,}$")
_PARENS = re.compile(r"\(.*?\)")


@dataclass(frozen=True)
class LosslessFile:
    provider: str
    username: str
    path: str                      # as the peer reported it, backslash separated
    extension: str
    size: int
    length_s: int | None
    bitrate_kbps: int | None
    sample_rate: int | None
    bit_depth: int | None
    has_free_slot: bool
    upload_speed_bps: int
    queue_length: int

    @property
    def name(self) -> str:
        return self.path.replace("\\", "/").rsplit("/", 1)[-1]

    @property
    def folder(self) -> str:
        parts = self.path.replace("\\", "/").rsplit("/", 1)
        return self.path[: len(self.path) - len(parts[-1]) - 1] if len(parts) == 2 else ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Reference:
    artist: str
    title: str
    mix_name: str | None
    duration_s: int | None
    deezer_id: int | None = None
    # The length of the thing the owner actually pointed at (the video), when it is known and the catalogue
    # disagrees with it. `match.score_candidate` already refuses to let a wrongly matched Beatport release
    # redefine that length for source candidates; without this the lossless gate was the one place that did.
    requested_duration_s: int | None = None
    # built from the request's own words rather than a catalogue record or a source candidate; a hit on it
    # tags from the request
    from_query: bool = False

    @property
    def is_original(self) -> bool:
        return self.mix_name is None or is_original(self.mix_name)

    @property
    def durations(self) -> tuple[int, ...]:
        """Every length this recording is known by, newest evidence first. `rank_duration` measures against
        the nearest; nothing rejects on it (issue #69)."""
        return tuple(dict.fromkeys(d for d in (self.duration_s, self.requested_duration_s) if d is not None))


def first_artist(artist: str) -> str:
    return _PARENS.sub("", artist).split(",")[0].strip()


def reference_for(catalog: CatalogTrack | None, cand: Candidate, requested_duration_s: int | None = None) -> Reference:
    if catalog is not None:
        return Reference(catalog.artist, catalog.title, catalog.mix_name, catalog.duration_s or cand.duration_s,
                         cand.deezer_id, requested_duration_s)
    title, version = parse_version(cand.title)
    return Reference(cand.artist, title, cand.mix_name or version or "Original Mix", cand.duration_s, cand.deezer_id,
                     requested_duration_s)


def search_text(ref: Reference) -> str:
    """What is typed into the network: first artist, title without parentheses, mix name only when it is a
    real version (the spike: this form found every track once the search was allowed to complete)."""
    text = f"{first_artist(ref.artist)} {_PARENS.sub('', ref.title)}"
    if not ref.is_original:
        text += f" {ref.mix_name}"
    return " ".join(text.split())


def file_title(name: str, artist: str) -> tuple[str, str | None]:
    """Normalised title tokens of a peer's file name with the artist's tokens removed, plus the version text
    `identify.parse_version` finds. Handles "02. A - T.flac", "09-a--t_x-6920ae5a.flac", "A_-_01_T.flac"."""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = re.sub(r"_+|--", " ", stem)
    stem = _TRAILING_HASH.sub("", _TRACK_NO.sub("", stem))
    seg = stem.split(" - ")[-1] if " - " in stem else stem
    seg = _TRACK_NO.sub("", seg)
    title, version = parse_version(seg.strip())
    artist_tokens = set(norm(artist).split())
    tokens = [t for t in norm(title).split() if t not in artist_tokens]
    return " ".join(tokens), version


@dataclass(frozen=True)
class PickPolicy:
    lossless_extensions: frozenset[str] = LOSSLESS_EXTENSIONS
    max_queue_length: int | None = None
    banned_users: frozenset[str] = frozenset()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["lossless_extensions"] = sorted(self.lossless_extensions)
        d["banned_users"] = sorted(self.banned_users)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> PickPolicy:
        """Tolerant of fields an older report carries and this policy no longer has (the replay re-reads
        every stored report, and the oldest of them predate half of these fields)."""
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in d.items() if k in known}
        kw["lossless_extensions"] = frozenset(kw.get("lossless_extensions", LOSSLESS_EXTENSIONS))
        kw["banned_users"] = frozenset(kw.get("banned_users", ()))
        return cls(**kw)


def transfer_ceiling_s(size: int, settings: Settings) -> float:
    """The longest one transfer may run, whatever it is doing. One flat cap cannot serve both a 5 MB single
    and a 67 MB ten-minute FLAC: 600 s at a peer's honest 100 kB/s files the first and cancels the second
    with 7 MB left. What has to hold is a floor on the rate, so the ceiling grows with the file; the stall
    bound in `slskd.download` is what catches a transfer that has actually died."""
    return max(settings.lossless_transfer_s, size * 8 / (settings.lossless_min_rate_kbps * 1000))


def policy_from_settings(settings: Settings) -> PickPolicy:
    return PickPolicy(max_queue_length=settings.lossless_max_queue)


Rule = Callable[[LosslessFile, Reference, PickPolicy], str | None]


def rule_extension(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    return None if f.extension in p.lossless_extensions else f"extension {f.extension!r}"


def rule_has_length(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    return "no length reported" if f.length_s is None else None


def rule_plausible_size(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    if f.sample_rate and f.sample_rate > MAX_SAMPLE_RATE:
        return f"sample rate {f.sample_rate} above {MAX_SAMPLE_RATE}"
    lo, hi = SIZE_KBPS.get(f.extension, (400, 4700))
    kbps = f.size * 8 / 1000 / max(f.length_s or 1, 1)
    if not lo <= kbps <= hi:
        return f"{kbps:.0f} kbps does not fit a {f.extension} of {f.length_s} s"
    return None


def rule_queue(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    if p.max_queue_length is not None and f.queue_length > p.max_queue_length:
        return f"queue {f.queue_length} > {p.max_queue_length}"
    return None


def rule_banned_user(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    return "banned user" if f.username in p.banned_users else None


Ranker = Callable[[LosslessFile, Reference], object]
DURATION_BUCKET_S = 5          # encoding slack between releases of one recording; inside it, peer quality decides


def _version_agrees(f: LosslessFile, ref: Reference) -> bool:
    """Does the file's name claim the version the reference is? Until #69 this was a rejection; a file that
    names the wrong mix is now merely the last thing to try, because only the fingerprint can settle it."""
    title, version = file_title(f.name, ref.artist)
    extra = [t for t in title.split() if t not in set(norm(ref.title).split())]
    words = set(norm(version or "").split()) | set(extra)
    if ref.is_original:
        return not (words & VERSION_WORDS) or "original" in words
    want = [w for w in norm(ref.mix_name).split() if w not in VERSION_WORDS]
    have = set(norm(f"{version or ''} {title}").split())
    return all(w in have for w in want)


def rank_version(f: LosslessFile, ref: Reference) -> int:
    return 0 if _version_agrees(f, ref) else 1


def rank_duration(f: LosslessFile, ref: Reference) -> int:
    """Distance to the nearest length the recording is known by, in buckets. Length is not identity (a
    626 s file was the 392 s video's recording, and two wrong files sat within 2 s of theirs), so it orders
    what to try first and the fingerprint decides (issue #69)."""
    known = ref.durations
    if not known or f.length_s is None:
        return 10_000
    return min(abs(f.length_s - d) for d in known) // DURATION_BUCKET_S


def rank_title(f: LosslessFile, ref: Reference) -> int:
    title, _ = file_title(f.name, ref.artist)
    return -(int(fuzz.token_set_ratio(norm(ref.title), title)) // 10)


def rank_artist(f: LosslessFile, ref: Reference) -> int:
    return 0 if norm(first_artist(ref.artist)) in norm(f.path) else 1


# Spec §5: the hard rules reject only what can never satisfy the goal, and everything about identity ranks
# survivors instead. `flackey replay-picks` measured this order against the 216 stored no-pick reports
# (docs/research/2026-09-21-no-pick-replay.md) before it became the live default.
HARD_RULES: list[tuple[str, Rule]] = [
    ("extension", rule_extension), ("has_length", rule_has_length), ("plausible_size", rule_plausible_size),
    ("queue", rule_queue), ("banned_user", rule_banned_user),
]
IDENTITY_RANKERS: list[tuple[str, Ranker]] = [
    ("version", rank_version), ("duration", rank_duration), ("title", rank_title), ("artist", rank_artist),
]
PEER_RANKERS: list[tuple[str, Ranker]] = [
    ("free_slot", lambda f, ref: not f.has_free_slot),
    ("bit_depth", lambda f, ref: {16: 0, 24: 1}.get(f.bit_depth or 0, 2)),   # CD master first; unknown last
    ("queue_length", lambda f, ref: f.queue_length),
    ("upload_speed", lambda f, ref: -f.upload_speed_bps),
    ("size", lambda f, ref: f.size),
]

# What `pick` uses unless a caller says otherwise. Both are bound as `pick`'s defaults below, so they must
# stay above it: identity orders the survivors, then the peer's ability to actually send the file.
RULES: list[tuple[str, Rule]] = HARD_RULES
RANKERS: list[tuple[str, Ranker]] = IDENTITY_RANKERS + PEER_RANKERS


@dataclass
class Rejection:
    file: LosslessFile
    rule: str
    reason: str


@dataclass
class PickReport:
    reference: Reference
    policy: PickPolicy
    seen: int
    rejections: list[Rejection] = field(default_factory=list)
    survivors: list[LosslessFile] = field(default_factory=list)
    chosen: LosslessFile | None = None
    summary: str = ""

    def to_dict(self) -> dict:
        return {"reference": asdict(self.reference), "policy": self.policy.to_dict(), "seen": self.seen,
                "rejections": [{"file": r.file.to_dict(), "rule": r.rule, "reason": r.reason} for r in self.rejections],
                "survivors": [f.to_dict() for f in self.survivors],
                "chosen": self.chosen.to_dict() if self.chosen else None, "summary": self.summary}

    @classmethod
    def from_dict(cls, d: dict) -> PickReport:
        return cls(reference=Reference(**d["reference"]), policy=PickPolicy.from_dict(d["policy"]), seen=d["seen"],
                   rejections=[Rejection(LosslessFile(**r["file"]), r["rule"], r["reason"]) for r in d["rejections"]],
                   survivors=[LosslessFile(**f) for f in d["survivors"]],
                   chosen=LosslessFile(**d["chosen"]) if d["chosen"] else None, summary=d["summary"])


def pick(files: list[LosslessFile], ref: Reference, policy: PickPolicy,
         rules: list[tuple[str, Rule]] = RULES, rankers: list[tuple[str, Ranker]] = RANKERS) -> PickReport:
    report = PickReport(reference=ref, policy=policy, seen=len(files))
    for f in files:
        for name, rule in rules:
            reason = rule(f, ref, policy)
            if reason:
                report.rejections.append(Rejection(f, name, reason))
                break
        else:
            report.survivors.append(f)
    report.survivors.sort(key=lambda f: tuple(fn(f, ref) for _, fn in rankers))
    report.chosen = report.survivors[0] if report.survivors else None
    counts = Counter(r.rule for r in report.rejections)
    parts = [f"{n} {rule}" for rule, n in counts.most_common()]
    head = f"{len(files)} files: " + (", ".join(parts) if parts else "")
    if report.chosen:
        c = report.chosen
        tail = f"chose {c.username} ({'slot' if c.has_free_slot else 'no slot'}, q{c.queue_length}, {c.extension})"
    else:
        tail = "nothing left"
    report.summary = f"{head}; {tail}" if parts else f"{head}{tail}"
    return report
