"""Shadow arbitration between the readings of a noisy YouTube title (`identify.interpretations`).

Nothing here changes what a request searches for or files. Each reading is put to Deezer's public search,
the records its words would accept are checked against the video, and the request is labelled with what
that showed: one reading's record *is* the video (`confirmed`), the readings lead to the same record
anyway (`agreed`), they lead to different ones and the audio could not settle it (`ambiguous`), or the
comparison could not run (`skipped`). Counting those is the point: `ambiguous` is how often asking the
owner which reading they meant would actually be needed."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace

from rapidfuzz import fuzz

from ..fingerprint import AcousticReference
from ..identify import parse_version
from ..lossless import Reference
from ..match import LENGTH_SLACK_S, THRESHOLD, candidate_version, score_candidate, versions_agree
from ..models import TITLE_FLOOR, Candidate, Query, Request, is_original, norm
from ..reference import DeezerSearchError, Identification, identify_record, search_deezer
from .records import query_candidate

log = logging.getLogger(__name__)

CONFIRMED, AGREED, AMBIGUOUS, SKIPPED = "confirmed", "agreed", "ambiguous", "skipped"
SEARCH_LIMIT = 10
# Previews fingerprinted per request across all readings. The pipeline's own identification tries up to
# IDENTIFY_MAX; this is a second look on top, so it is bounded on its own.
MAX_PREVIEWS = 8
# The whole comparison, Deezer and fpcalc included (the video is not: the pass fetches that anyway). Past it
# the request is labelled `skipped` and carries on -- a slow Deezer must never be what holds a download up.
BUDGET_S = 45


@dataclass
class ReadingResult:
    """What one reading's words found on Deezer: the records they would accept, best first."""
    reading: Query
    accepted: list[Candidate] = field(default_factory=list)
    error: str | None = None


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
    scored: list[Candidate] = []
    for c in found:
        c = replace(c)
        q = reading if reading.artist else replace(reading, artist=c.artist)
        c.score = score_candidate(q, c, None).total
        if c.score < THRESHOLD or fuzz.token_set_ratio(norm(reading.title), norm(parse_version(c.title)[0])) < TITLE_FLOOR:
            continue
        if not reading.artist and not _fits(c, reading.duration_s):
            continue
        scored.append(c)
    if reading.version:
        out = [c for c in scored if versions_agree(reading.version, candidate_version(c))]
    else:
        originals = [c for c in scored if is_original(candidate_version(c))]
        length_held = any(_fits(c, reading.duration_s) for c in originals)
        out = originals + [c for c in scored if c not in originals and not length_held
                           and _fits(c, reading.duration_s)]
    return sorted(out, key=lambda c: (-(c.score or 0), c.rank))


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
    """The previews to fingerprint: each reading's best record, then each one's second best, and so on --
    so the budget is shared rather than spent on the first reading's list -- once per Deezer id, and never
    one the pipeline's own identification has already compared."""
    out: list[Candidate] = []
    seen = set(skip)
    depth = max((len(r.accepted) for r in results), default=0)
    for i in range(depth):
        for r in results:
            if i < len(r.accepted) and r.accepted[i].deezer_id not in seen and len(out) < limit:
                seen.add(r.accepted[i].deezer_id)
                out.append(r.accepted[i])
    return out


def classify(results: list[ReadingResult], matched: int | None) -> Outcome:
    """The label, from what each reading found and which Deezer record (if any) the video matched.

    A match is attributed to the first reading that accepted that record. Without one, a search that failed
    leaves the readings unfinished and the label is `skipped`; readings that are the same words split
    differently search for the same thing and are `agreed` by construction; otherwise every reading that
    found a record must have found the same one. Readings that found nothing at all are `ambiguous`: the
    catalogue says nothing about which is right, which is exactly when asking would be needed."""
    if matched is not None:
        for i, r in enumerate(results):
            if any(c.deezer_id == matched for c in r.accepted):
                return Outcome(CONFIRMED, i, f"deezer:{matched} is the video")
    failed = [r for r in results if r.error]
    if failed:
        return Outcome(SKIPPED, None, f"{len(failed)} of {len(results)} Deezer searches failed")
    if len({_words(r.reading) for r in results}) == 1:
        return Outcome(AGREED, None, "the readings are the same words")
    tops = [r.accepted[0] for r in results if r.accepted]
    if not tops:
        return Outcome(AMBIGUOUS, None, "no reading found a record")
    if all(same_record(tops[0], t) for t in tops[1:]):
        return Outcome(AGREED, None, f"{len(tops)} of {len(results)} readings found a record, all the same one")
    return Outcome(AMBIGUOUS, None, f"{len(tops)} readings found different records")


def _label(q: Query) -> str:
    text = f"{q.artist} - {q.title}" if q.artist else f"? - {q.title}"
    return f"{text} ({q.version})" if q.version else text


def _summary(i: int, r: ReadingResult) -> str:
    if r.error:
        hit = f"search failed: {r.error}"
    elif r.accepted:
        top = r.accepted[0]
        hit = f"deezer:{top.deezer_id} {top.artist} - {top.title} ({len(r.accepted)} accepted)"
    else:
        hit = "none"
    return f'r{i} "{_label(r.reading)}": {hit}'


class InterpretMixin:
    """Part of `Worker`, which supplies the state these methods read."""

    async def _interpret(self, req: Request, video: AcousticReference | None, ident: Identification | None = None,
                         *, fetch: bool = False) -> None:
        """Label a request whose title had more than one reading; once per request, and never anything
        else. `video` is the reference `_identify` already holds; `fetch` lets this get it through
        `_video_reference` instead, which stores it, so the fingerprint step after reads it back rather than
        downloading it again. `ident` is the pipeline's own identification, whose previews are not fetched
        twice. Never raises, and the comparison never takes longer than BUDGET_S."""
        readings = self.store.get_interpretations(req.id)
        if len(readings) < 2 or self.store.get_interpretation_outcome(req.id)[0] is not None:
            return
        try:
            if video is None and fetch:
                video, _ = await self._video_reference(req)
            async with asyncio.timeout(BUDGET_S):
                outcome, results, tried = await self._arbitrate(req, readings, video, ident)
        except TimeoutError:
            outcome, results, tried = Outcome(SKIPPED, None, f"took longer than {BUDGET_S} s"), [], 0
        except Exception:
            # Shadow work: a bug here is a missing label, never a failed request.
            log.exception("req#%d: comparing the title's readings failed", req.id)
            return
        self.store.set_interpretation_outcome(req.id, outcome.klass, outcome.reading)
        detail = " | ".join(_summary(i, r) for i, r in enumerate(results)) or f"{len(readings)} readings"
        log.info("req#%d interpretations: %s%s (%s); previews %d | %s", req.id, outcome.klass,
                 f" by r{outcome.reading}" if outcome.reading is not None else "", outcome.why, tried, detail)

    async def _arbitrate(self, req: Request, readings: list[Query], video: AcousticReference | None,
                         ident: Identification | None) -> tuple[Outcome, list[ReadingResult], int]:
        if video is None:
            return Outcome(SKIPPED, None, "no video audio to compare against"), [], 0
        results: list[ReadingResult] = []
        searched: dict[str, list[Candidate] | str] = {}   # readings that are the same words search once
        for reading in readings:
            # `search_text` falls back to the whole raw title when there is no artist; a reading without one
            # is the title alone, so that is what it searches
            text = reading.search_text() if reading.artist else " ".join(filter(None, (reading.title, reading.version)))
            if text not in searched:
                try:
                    searched[text] = await search_deezer(text, self.http, limit=SEARCH_LIMIT)
                except DeezerSearchError as e:
                    searched[text] = str(e)
            found = searched[text]
            results.append(ReadingResult(reading, error=found) if isinstance(found, str)
                           else ReadingResult(reading, accepted(reading, found)))
        # The pipeline has compared these previews already; its match is a match for any reading that
        # accepts the same record, and its misses need no second download.
        if ident is not None and ident.chosen is not None and ident.chosen.deezer_id is not None:
            outcome = classify(results, ident.chosen.deezer_id)
            if outcome.klass == CONFIRMED:
                return outcome, results, 0
        known = {deezer_id for deezer_id, _ in ident.tried} if ident is not None else set()
        previews = preview_order(results, known)
        if not previews:
            return classify(results, None), results, 0
        async with self._cpu:
            check = await identify_record(video, previews, self.http, self.settings.tmp_dir,
                                          minimum=self.settings.lossless_fingerprint_min, limit=len(previews))
        matched = check.chosen.deezer_id if check.chosen is not None else None
        return classify(results, matched), results, len(check.tried)

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
