"""Shadow arbitration between the readings of a noisy YouTube title (`identify.interpretations`).

Nothing here changes what a request searches for or files. Each reading is put to Deezer's public search,
the records its words name are checked against the video, and the request is labelled with what
that showed: one reading's record *is* the video (`confirmed`), the readings lead to the same record
anyway (`agreed`), they lead to different ones and the audio could not settle it (`ambiguous`), or the
comparison could not run (`skipped`). Counting those is the point: `ambiguous` is how often asking the
owner which reading they meant would actually be needed.

It runs beside the request, never in its way: a background task per request, one comparison at a time,
Deezer's API called at a measured pace and the CPU slot held for fpcalc alone."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace

from rapidfuzz import fuzz

from ..fingerprint import AcousticReference
from ..identify import parse_version
from ..lossless import Reference
from ..match import (
    LENGTH_SLACK_S,
    THRESHOLD,
    W_ARTIST,
    W_TITLE,
    Score,
    candidate_version,
    score_candidate,
    versions_agree,
)
from ..models import TITLE_FLOOR, Candidate, Query, Request, is_original, norm
from ..reference import DeezerSearchError, Identification, identify_record, search_deezer
from .records import query_candidate

log = logging.getLogger(__name__)

CONFIRMED, AGREED, AMBIGUOUS, SKIPPED = "confirmed", "agreed", "ambiguous", "skipped"
SEARCH_LIMIT = 10
# Previews fingerprinted per request across all readings. The pipeline's own identification tries up to
# IDENTIFY_MAX; this is a second look on top, so it is bounded on its own.
MAX_PREVIEWS = 8
# One comparison, Deezer and fpcalc included, once it has its turn. Past it the label is `skipped`.
BUDGET_S = 45
# Between two of this module's calls to api.deezer.com. Deezer allows about 50 requests in 5 s per address,
# and the pipeline's own lookups (a Spotify link, a request the bot could not place) share that allowance:
# a noisy playlist compared at full speed would spend it for them.
DEEZER_INTERVAL_S = 0.15
# The least the artist and title points (`score_candidate`'s, out of W_ARTIST + W_TITLE) may add up to for a
# record to be one the reading's words name: THRESHOLD's share of them. "D'rude" for "Darude" is named;
# someone else's song of the same title is not.
WORDS_MIN = round((W_ARTIST + W_TITLE) * THRESHOLD / 100)


@dataclass
class ReadingResult:
    """What one reading's words found on Deezer: the records they would accept, best first, and every
    record they name (`named`), which is what the video is compared with."""
    reading: Query
    accepted: list[Candidate] = field(default_factory=list)
    error: str | None = None
    named: list[Candidate] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.named:
            self.named = list(self.accepted)


@dataclass(frozen=True)
class Outcome:
    klass: str
    reading: int | None      # the reading the video confirmed; None for every other class
    why: str


def accepted(reading: Query, found: list[Candidate]) -> list[Candidate]:
    """The records among `found` this reading's words would take, best first, by `decide`'s own parts: the
    score's THRESHOLD, the requested version (or, with none requested, an original -- or the one version the
    video's length singles out, as `decide` pins it). Two additions for a search that, unlike the bot's
    menu, returns the artist's other tracks too: the title has to agree (TITLE_FLOOR), because artist,
    version and length alone outscore the threshold. And a reading with no artist (a title whose channel
    may be a label's) is scored as though the artist agreed and then has to match the video's length
    instead: half of what names a recording is missing, and the length is the other fact that is not a word."""
    scored = [c for c, _ in _scored(reading, found) if (c.score or 0) >= THRESHOLD]
    if reading.version:
        out = [c for c in scored if versions_agree(reading.version, candidate_version(c))]
    else:
        originals = [c for c in scored if is_original(candidate_version(c))]
        length_held = any(_fits(c, reading.duration_s) for c in originals)
        out = originals + [c for c in scored if c not in originals and not length_held
                           and _fits(c, reading.duration_s)]
    return sorted(out, key=lambda c: (-(c.score or 0), c.rank))


def named(reading: Query, found: list[Candidate]) -> list[Candidate]:
    """The records among `found` whose artist and title are this reading's words, whatever their version
    and length: the ones worth putting to the video. `accepted`'s come first, as it orders them, then the
    rest by the same score -- so the length still decides what is heard first, but no longer whether.

    The pipeline never lets the text score decide which record a video is: with a video it fingerprints
    the bot's records in score order and the audio answers. Hearing only `accepted`'s left this comparison
    deaf where it matters most. A music video runs longer than any release of its song (an intro, an
    outro), so the original scores below THRESHOLD on length and the radio edit it is cut from on version
    as well; no preview was fetched, and a title the audio would have settled at once was `ambiguous`."""
    first = accepted(reading, found)
    taken = {(c.deezer_id, c.rank) for c in first}
    rest = [c for c, s in _scored(reading, found)
            if (c.deezer_id, c.rank) not in taken and s.artist + s.title >= WORDS_MIN]
    return first + sorted(rest, key=lambda c: (-(c.score or 0), c.rank))


def _scored(reading: Query, found: list[Candidate]) -> list[tuple[Candidate, Score]]:
    """Each record of `found` that names the reading's title, scored against the reading (on a copy). A
    reading with no artist borrows the record's and must match the video's length instead (see `accepted`)."""
    out: list[tuple[Candidate, Score]] = []
    for c in found:
        if fuzz.token_set_ratio(norm(reading.title), norm(parse_version(c.title)[0])) < TITLE_FLOOR:
            continue
        if not reading.artist and not _fits(c, reading.duration_s):
            continue
        c = replace(c)
        s = score_candidate(reading if reading.artist else replace(reading, artist=c.artist), c, None)
        c.score = s.total
        out.append((c, s))
    return out


def _fits(c: Candidate, duration_s: int | None) -> bool:
    return duration_s is not None and c.duration_s is not None and abs(c.duration_s - duration_s) <= LENGTH_SLACK_S


def same_record(a: Candidate, b: Candidate) -> bool:
    """One recording: the same Deezer record, the same ISRC, or the same words on two releases of it."""
    if a.deezer_id is not None and a.deezer_id == b.deezer_id:
        return True
    if a.isrc and a.isrc == b.isrc:
        return True
    return all(set(norm(x).split()) == set(norm(y).split()) for x, y in
               ((a.artist, b.artist), (a.title, b.title), (candidate_version(a), candidate_version(b))))


def _words(q: Query) -> frozenset[str]:
    return frozenset(norm(f"{q.artist or ''} {q.title or ''} {q.version or ''}").split())


def preview_order(results: list[ReadingResult], skip: set[int], limit: int = MAX_PREVIEWS) -> list[Candidate]:
    """The previews to fingerprint: every record a reading accepts before any it only names, and within
    each, each reading's first, then each one's second, and so on -- so the budget is shared rather than
    spent on the first reading's list -- once per Deezer id, and never one the pipeline's own
    identification has already compared."""
    out: list[Candidate] = []
    seen = set(skip)
    accepted_ids = {c.deezer_id for r in results for c in r.accepted}
    for pools in ([r.accepted for r in results],
                  [[c for c in r.named if c.deezer_id not in accepted_ids] for r in results]):
        for i in range(max((len(p) for p in pools), default=0)):
            for pool in pools:
                if i < len(pool) and pool[i].deezer_id not in seen and len(out) < limit:
                    seen.add(pool[i].deezer_id)
                    out.append(pool[i])
    return out


def _words_points(reading: Query, c: Candidate) -> float:
    """How exactly the reading's own words name `c`, artist plus title. token_sort, not the scoring's
    token_set: that one gives "Alpha x Beta" and "Alpha" both 100 for a record by Alpha, and telling those
    two apart is the point here. A reading with no artist has only its title to offer, so it is not
    credited with the record's artist it borrows for `accepted`."""
    title = fuzz.token_sort_ratio(norm(reading.title), norm(parse_version(c.title)[0]))
    return title + (fuzz.token_sort_ratio(norm(reading.artist), norm(c.artist)) if reading.artist else 0)


def classify(results: list[ReadingResult], matched: int | None) -> Outcome:
    """The label, from what each reading found and which Deezer record (if any) the video matched.

    A match is attributed to a reading that accepted that record, or failing that one whose words name it
    (the video's length or version was not the one `accepted` asks for, but the artist and the title it
    read were right); among several, the one whose artist and title name it best, then the earlier.
    Without one, a search that failed leaves the readings unfinished and the label is `skipped`; readings
    that are the same words split differently search for the same thing and are `agreed` by construction;
    otherwise every reading that found a record -- its best accepted one, or else its best named one --
    must have found the same one. Readings that named nothing at all are `skipped` too: Deezer not knowing
    a track is no evidence that its readings disagree, and the label exists to count those. Counted as
    `ambiguous`, every title the public catalogue lacks -- a label's upload, a bootleg, most of what
    Soulseek alone has -- read as one the owner would have to be asked about, whatever the pipeline then
    found and proved by audio."""
    if matched is not None:
        for pool in ("accepted", "named"):
            hits = [(_words_points(r.reading, c), -i) for i, r in enumerate(results)
                    for c in getattr(r, pool) if c.deezer_id == matched]
            if hits:
                return Outcome(CONFIRMED, -max(hits)[1], f"deezer:{matched} is the video")
    failed = [r for r in results if r.error]
    if failed:
        return Outcome(SKIPPED, None, f"{len(failed)} of {len(results)} Deezer searches failed")
    if len({_words(r.reading) for r in results}) == 1:
        return Outcome(AGREED, None, "the readings are the same words")
    # A music video's readings often accept nothing (no release is its length) yet name records all the
    # same: those still say whether the readings lead to one recording or to two.
    tops = [(r.accepted or r.named)[0] for r in results if r.named]
    if not tops:
        return Outcome(SKIPPED, None, "no reading found a record to tell them apart by")
    if all(same_record(tops[0], t) for t in tops[1:]):
        return Outcome(AGREED, None, f"{len(tops)} of {len(results)} readings found a record, all the same one")
    return Outcome(AMBIGUOUS, None, f"{len(tops)} readings found different records")


def _label(q: Query) -> str:
    text = f"{q.artist} - {q.title}" if q.artist else f"? - {q.title}"
    return f"{text} ({q.version})" if q.version else text


def _summary(i: int, r: ReadingResult) -> str:
    if r.error:
        hit = f"search failed: {r.error}"
    elif r.named:
        top = (r.accepted or r.named)[0]
        hit = (f"deezer:{top.deezer_id} {top.artist} - {top.title} "
               f"({len(r.accepted)} accepted, {len(r.named)} named)")
    else:
        hit = "none"
    return f'r{i} "{_label(r.reading)}": {hit}'


class _OffTheClock:
    """A semaphore held like one, except that waiting for it stops `budget`'s clock. The budget is for the
    comparison's own work; a slot the real requests are busy with is not that."""

    def __init__(self, slot: asyncio.Semaphore, budget: asyncio.Timeout):
        self.slot, self.budget = slot, budget

    async def __aenter__(self) -> None:
        loop = asyncio.get_running_loop()
        deadline, start = self.budget.when(), loop.time()
        self.budget.reschedule(None)
        try:
            await self.slot.acquire()
        finally:
            if deadline is not None:
                self.budget.reschedule(deadline + loop.time() - start)

    async def __aexit__(self, *exc: object) -> None:
        self.slot.release()


class InterpretMixin:
    """Part of `Worker`, which supplies the state these methods read -- including `_shadow` (one comparison
    at a time), `_shadow_tasks` and `_deezer_last`."""

    def _interpret(self, req: Request, video: AcousticReference | None, ident: Identification | None = None) -> None:
        """Start labelling a request whose title had more than one reading, in the background: the request
        never waits for it. Once per request. `video` is the reference the pass already holds -- never
        fetched here -- and `ident` the pipeline's own identification, whose previews are not fetched twice.

        No video, no label: a yt-dlp failure says nothing about the title, and the next pass may have the
        video. Such a request counts as pending until one does."""
        if req.id in self._shadow_tasks:
            return
        readings = self.store.get_interpretations(req.id)
        if len(readings) < 2 or self.store.get_interpretation_outcome(req.id)[0] is not None:
            return
        if video is None:
            log.info("req#%d interpretations: not compared this pass, no video audio", req.id)
            return
        task = asyncio.create_task(self._compare(req.id, readings, video, ident))
        self._shadow_tasks[req.id] = task
        task.add_done_callback(lambda t, rid=req.id: self._shadow_done(rid, t))

    def _shadow_done(self, request_id: int, task: asyncio.Task) -> None:
        if self._shadow_tasks.get(request_id) is task:     # not a newer comparison for the same request
            del self._shadow_tasks[request_id]
        # `_compare` catches what it can see coming; anything else would otherwise surface only as
        # "Task exception was never retrieved", with no request id on it.
        if not task.cancelled() and task.exception() is not None:
            log.error("req#%d: comparing the title's readings failed", request_id, exc_info=task.exception())

    def forget_comparison(self, request_id: int) -> None:
        """A stopped or removed request is not worth comparing: drop its comparison, queued or running."""
        task = self._shadow_tasks.pop(request_id, None)
        if task is not None:
            task.cancel()

    async def _shadow_settled(self) -> None:
        """Wait for every comparison started so far (tests, and nothing else, need to)."""
        while self._shadow_tasks:
            await asyncio.gather(*list(self._shadow_tasks.values()), return_exceptions=True)

    async def _compare(self, request_id: int, readings: list[Query], video: AcousticReference,
                       ident: Identification | None) -> None:
        async with self._shadow:
            try:
                async with asyncio.timeout(BUDGET_S) as budget:
                    outcome, results, tried = await self._arbitrate(readings, video, ident, budget)
            except TimeoutError:
                outcome, results, tried = Outcome(SKIPPED, None, f"took longer than {BUDGET_S} s"), [], 0
            except Exception:
                # Shadow work: a bug here is a missing label, never a failed request.
                log.exception("req#%d: comparing the title's readings failed", request_id)
                return
        try:
            self.store.set_interpretation_outcome(request_id, outcome.klass, outcome.reading)
        except Exception:
            log.exception("req#%d: could not record the comparison of the title's readings", request_id)
            return
        detail = " | ".join(_summary(i, r) for i, r in enumerate(results)) or f"{len(readings)} readings"
        log.info("req#%d interpretations: %s%s (%s); previews %d | %s", request_id, outcome.klass,
                 f" by r{outcome.reading}" if outcome.reading is not None else "", outcome.why, tried, detail)

    async def _deezer_turn(self) -> None:
        """Wait until DEEZER_INTERVAL_S has passed since this module last called Deezer's API."""
        loop = asyncio.get_running_loop()
        wait = self._deezer_last + DEEZER_INTERVAL_S - loop.time()
        if wait > 0:
            await asyncio.sleep(wait)
        self._deezer_last = loop.time()

    async def _arbitrate(self, readings: list[Query], video: AcousticReference, ident: Identification | None,
                         budget: asyncio.Timeout | None = None) -> tuple[Outcome, list[ReadingResult], int]:
        results: list[ReadingResult] = []
        searched: dict[str, list[Candidate] | str] = {}   # readings that are the same words search once
        for reading in readings:
            # `search_text` falls back to the whole raw title when there is no artist; a reading without one
            # is the title alone, so that is what it searches
            text = reading.search_text() if reading.artist else " ".join(filter(None, (reading.title, reading.version)))
            if text not in searched:
                await self._deezer_turn()
                try:
                    searched[text] = await search_deezer(text, self.http, limit=SEARCH_LIMIT)
                except DeezerSearchError as e:
                    searched[text] = str(e)
            found = searched[text]
            results.append(ReadingResult(reading, error=found) if isinstance(found, str)
                           else ReadingResult(reading, accepted(reading, found), named=named(reading, found)))
        # The pipeline has compared these previews already; its match is a match for any reading that
        # accepts the same record, and its misses need no second download.
        if ident is not None and ident.chosen is not None and ident.chosen.deezer_id is not None:
            outcome = classify(results, ident.chosen.deezer_id)
            if outcome.klass == CONFIRMED:
                return outcome, results, 0
        known = {deezer_id for deezer_id, _ in ident.tried} if ident is not None else set()
        # The CPU slot is the real requests' first: time spent waiting for it is theirs, not this budget's
        cpu = self._cpu if budget is None else _OffTheClock(self._cpu, budget)
        tried, unscored = 0, 0
        accepted_ids = {c.deezer_id for r in results for c in r.accepted}
        for cand in preview_order(results, known):
            # One at a time, so each preview's Deezer lookup takes its turn, and the CPU slot is held for
            # fpcalc alone -- never across a download, where it would starve the real requests' checks.
            await self._deezer_turn()
            check = await identify_record(video, [cand], self.http, self.settings.tmp_dir,
                                          minimum=self.settings.lossless_fingerprint_min, limit=1, cpu=cpu)
            tried += 1
            if check.chosen is not None:
                return classify(results, check.chosen.deezer_id), results, tried
            # Only an accepted record's: a record a reading merely names is an extra listen, and Deezer
            # has no preview for plenty of them; one missing must not overrule what the rest showed.
            unscored += sum(score is None for _, score in check.tried) if cand.deezer_id in accepted_ids else 0
        outcome = classify(results, None)
        if unscored:
            # A preview that could not be fetched (Deezer refusing the lookup, a download failing) might have
            # been the match: what the readings found cannot be told apart from what went unchecked.
            outcome = Outcome(SKIPPED, None, f"{unscored} of {tried} previews could not be checked")
        return outcome, results, tried

    def _reading_spellings(self, req: Request) -> list[Candidate]:
        """The other readings of the title as stand-in candidates, for Soulseek's spelling ladder to try
        after every spelling it already had. A reading with no artist is left out, as the request's own
        words are when they have none: a title alone is too loose a search."""
        return [query_candidate(r, req.id) for r in self.store.get_interpretations(req.id)[1:]
                if r.artist and r.title]

    def _reading_that_found(self, req: Request, ref: Reference) -> Query | None:
        """The reading, other than the one the request searches with, whose spelling `ref` is -- so a file an
        alternative reading found is named after the words that found it, not the ones that did not."""
        readings = self.store.get_interpretations(req.id)
        if not readings or ref == self._reference(req, query_candidate(readings[0], req.id), None):
            return None
        return next((r for r in readings[1:] if r.artist and r.title
                     and ref == self._reference(req, query_candidate(r, req.id), None)), None)
