"""Identify a request, choose its record, and hand it to fetch, verify and file."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..catalog import CatalogUnavailable, best_match
from ..identify import parse_text
from ..library import find_duplicate
from ..match import candidate_query, candidate_version, decide, same_version
from ..models import Candidate, CatalogTrack, Query, Request, RequestState
from ..notify import Button
from ..reference import Identification, identify_record
from ..source import SourceError, SourceNotFound, SourceTimeout, SourceUnauthorized
from .policy import MAX_ATTEMPTS, REVIEW_BUTTONS
from .records import CATALOG_SOURCE, QUERY_SOURCE, _mmss, catalog_candidate, query_candidate

log = logging.getLogger(__name__)


class PipelineMixin:
    """Part of `Worker`, which supplies the state these methods read."""

    async def _process(self, request_id: int) -> None:
        req = self.store.get_request(request_id)
        query = req.query()
        if query.artist is None and query.title is None:
            query = parse_text(req.raw_text)
            self.store.update_request(req.id, query_artist=query.artist, query_title=query.title,
                                      query_version=query.version)

        if req.chosen_candidate_id is not None:
            cand = self.store.get_candidate(req.chosen_candidate_id)
            catalog = self.store.get_catalog_track(req.catalog_track_id) if req.catalog_track_id else None
        else:
            picked = await self._identify(req, query)
            if picked is None:
                return
            cand, catalog = picked
        try:
            catalog = await self._catalog_for(req, cand, catalog)
        except CatalogUnavailable as e:
            await self._retry_or_fail(req, f"Beatport unreachable: {e}", flag="Beatport unreachable, will retry")
            return

        await self._fetch_verify_file(req, cand, catalog)

    async def _identify(self, req: Request, query: Query) -> tuple[Candidate, CatalogTrack | None] | None:
        """Search Beatport and the source, choose the record, and persist the choice. Answers with the
        candidate and catalog match to fetch, or None when this pass has already been settled here --
        retried, parked for review, a duplicate, or handed to the search on the request's own words."""
        self._set_state(req, RequestState.IDENTIFYING)
        try:
            catalog_tracks = await self.catalog.search(query)
            catalog = best_match(query, catalog_tracks)
        except CatalogUnavailable as e:
            await self._retry_or_fail(req, f"Beatport unreachable: {e}", flag="Beatport unreachable, will retry")
            return None
        if catalog:
            self.store.upsert_catalog_track(catalog)
            self.store.update_request(req.id, catalog_track_id=catalog.id)

        cands: list[Candidate] = []
        # What to tell the owner if nothing identifies the track. "No Deezer candidates" covers three
        # different situations and the row used to say which, so it still does: a bot that is switched
        # off is the owner's own setting, and the bot's own sentence is how a not-found is diagnosed.
        source_why = "the Deezer bot is switched off"
        if self.settings.source_enabled:
            source_why = "no Deezer candidates"
            try:
                cands = await self.source.search(query)
            except SourceUnauthorized:
                raise  # handled in process(): subclass of SourceError, so it must be caught before it
            except SourceNotFound as e:
                # Not a verdict any more: the request's own words can still reach Soulseek below.
                source_why = f"the Deezer bot found nothing ({e})"
                log.info("req#%d not found at source: %s", req.id, e)
            except (SourceTimeout, SourceError) as e:
                if catalog is None:
                    # The source may be back in an hour and would offer a lossy fallback the query-only
                    # path does not have; a retry is worth more than a Soulseek-or-nothing pass now.
                    await self._retry_or_fail(req, f"source error: {e}")
                    return None
                # Beatport knows the track, so the request is still actionable: fall through to the
                # catalog-only path rather than retrying a source that may be down for hours.
                log.info("req#%d source gave nothing (%s); trying the lossless providers on the "
                         "Beatport match alone", req.id, e)

        if not cands:
            # Either the source is switched off, or it found nothing, or it failed with a Beatport
            # match already in hand.
            if catalog is not None:
                # `catalog_candidate` explains what this costs; the short version is that the pick
                # rules still run entirely on Beatport data, but a request with no video of its own
                # then has nothing to fingerprint against and the attempt ends `fingerprint_unavailable`.
                if not self._lossless_allowed(req):
                    # Beatport knows the track, so it exists -- there is just no route to a file now.
                    await self._no_route(req)
                    return None
                await self._fetch_verify_file(req, catalog_candidate(catalog), catalog)
                return None
            await self._search_on_the_request(req, query, None, f"{source_why} and no Beatport match")
            return None

        # The record: by audio when the request has audio of its own, by text otherwise (issue #68).
        # `decide` runs either way: it scores every candidate (the order the previews are tried in,
        # and what the Choose window shows), and its verdict only counts without a video.
        video, why = await self._video_reference(req)
        if video is None and why.startswith("video: ") and req.attempts + 1 < MAX_ATTEMPTS:
            # yt-dlp fails for a minute (429, a network blip) far more often than for good, and the
            # video's audio is what identifies the record: a short wait beats choosing by text. After the
            # ladder the pass goes on without it, so a removed video cannot block the request.
            await self._retry_or_fail(req, f"could not fetch the video's audio: {why[7:]}",
                                      flag="video audio unavailable, will retry")
            return None
        decision = decide(query, cands, catalog)
        ident: Identification | None = None
        if video is not None:
            ordered = sorted(cands, key=lambda c: -(c.score or 0))
            async with self._cpu:
                ident = await identify_record(video, ordered, self.http, self.settings.tmp_dir,
                                              minimum=self.settings.lossless_fingerprint_min)
            log.info("req#%d identification: %s (tried %s)", req.id, ident.reason, ident.tried)
        chosen_obj = ident.chosen if ident is not None else decision.chosen
        if chosen_obj is not None and chosen_obj.isrc:
            # The source's recording ID disambiguates equally named Beatport releases. Never
            # substitute a loosely matched release: require the normal search score first.
            matched_catalog = best_match(query, catalog_tracks, chosen_obj.isrc)
            if matched_catalog and matched_catalog.id != (catalog.id if catalog else None):
                catalog = matched_catalog
                self.store.upsert_catalog_track(catalog)
                self.store.update_request(req.id, catalog_track_id=catalog.id)
                if ident is None:
                    decision = decide(query, cands, catalog)
                    chosen_obj = decision.chosen
        saved = self.store.add_candidates(req.id, cands)
        confidence = (round(ident.score * 100) if ident is not None and ident.score is not None
                      else (chosen_obj.score if chosen_obj else None))
        self.store.update_request(req.id, confidence=confidence)
        if chosen_obj is None:
            reason = ident.reason if ident is not None else decision.reason
            log.info("req#%d: no acceptable candidate among %d: %s", req.id, len(cands), reason)
            await self._search_on_the_request(req, query, catalog, reason)
            return None
        # the chooser returns one of the objects in `cands`; match by identity, not by source_ref
        # (the bot can list the same Deezer id twice)
        chosen = saved[next(i for i, c in enumerate(cands) if c is chosen_obj)]

        dup = find_duplicate(self.store, catalog, chosen)
        if dup:
            await self._mark_duplicate(req, dup.id, dup.path)
            return None

        if ident is None and not decision.auto:
            self._set_state(req, RequestState.AWAITING_REVIEW, flag_reason=decision.reason,
                            chosen_candidate_id=chosen.id)
            await self._ask_review(req, saved, decision.reason)
            return None
        # persist the choice so a fetch failure resumes here instead of searching (and saving candidates) again
        self.store.update_request(req.id, chosen_candidate_id=chosen.id)
        return chosen, catalog

    async def _catalog_for(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> CatalogTrack | None:
        """The catalog was matched for the video's title, i.e. for the original. When the candidate we are
        downloading is another version (the owner picked a remix, or the video's length pinned an edit),
        re-match Beatport against that version so the tags describe the recording in the file."""
        if catalog is None or same_version(cand, catalog):
            return catalog
        catalog = best_match(candidate_query(cand), await self.catalog.search(candidate_query(cand)))
        if catalog:
            self.store.upsert_catalog_track(catalog)
        self.store.update_request(req.id, catalog_track_id=catalog.id if catalog else None)
        return catalog


    async def _search_on_the_request(self, req: Request, query: Query, catalog: CatalogTrack | None,
                                     why: str) -> None:
        """No record was chosen. Soulseek is the only network that has this library's music, and the
        fingerprint is what proves the file; the owner's own words are enough to ask with (issue #69)."""
        if not (query.artist and query.title):
            self._set_state(req, RequestState.NOT_FOUND, error_message=f"could not identify this track: {why}; "
                            f"no artist and title could be read from the request, so there is nothing to search for")
            await self.notifier.send(f"Could not identify: {req.raw_text}")
            return
        if not self._lossless_allowed(req):
            await self._no_route(req)
            return
        await self._fetch_verify_file(req, query_candidate(query, req.id), catalog)


    async def _mark_duplicate(self, req: Request, track_id: int | None, path: Path) -> None:
        self._set_state(req, RequestState.DUPLICATE, track_id=track_id)
        if req.playlist_id is not None and track_id is not None:
            self.store.add_playlist_track(req.playlist_id, track_id, req.playlist_position or 0)
        await self.notifier.send(f"Already in library: {path}")


    async def _ask_review(self, req: Request, cands: list[Candidate], reason: str) -> None:
        head = f"Review needed: {req.raw_text}"
        if req.query_duration_s:
            head += f" · Video: {_mmss(req.query_duration_s)}"  # so a wrong-length pick is visible at a glance
        lines = [head]
        buttons: list[Button] = []
        # numbered 1..n in *display* order (best first); c.rank is the bot's menu order and would jump around
        for n, c in enumerate(sorted(cands, key=lambda c: (-(c.score or 0), c.rank))[:REVIEW_BUTTONS], start=1):
            lines.append(f"{n}. {c.artist} – {c.title} ({candidate_version(c)}) · {_mmss(c.duration_s)} · {c.score or 0}%")
            buttons.append(Button(str(n), f"pick:{req.id}:{c.id}"))
        lines.append(f"Reason: {reason}")
        buttons.append(Button("Cancel", f"cancel:{req.id}"))
        await self.notifier.send("\n".join(lines), buttons)


    async def _fetch_verify_file(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> None:
        dup = find_duplicate(self.store, catalog, cand)  # again: a reviewed request may have waited for hours
        if dup:
            await self._mark_duplicate(req, dup.id, dup.path)
            return
        self._set_state(req, RequestState.FETCHING)
        # Once per request, before anything downloads: the reference is what every pick below, the lossy
        # fallback and any later retry are checked against, and it does not change between them.
        acoustic = await self._acoustic_reference(req, cand)
        if acoustic.reference is None and acoustic.missing.startswith("video: ") and req.attempts + 1 < MAX_ATTEMPTS:
            # The same quick ladder `_process` gives a request whose record is chosen by audio, for the route
            # that has no record at all. Since issue #69 the catalogue-less path is how a track Beatport has
            # never heard of gets filed, and on it the video is the *only* reference: a stand-in candidate has
            # no Deezer preview to fall back on, so a yt-dlp blip used to end the request in `error` on its
            # first pass -- after a real search and a download it then had nothing to check. Only a fetch that
            # failed ("video: "), never audio that arrived unreadable, and only while the ladder has budget:
            # after it the pass goes on and ends `fingerprint_unavailable` as before.
            await self._retry_or_fail(req, f"could not fetch the video's audio: {acoustic.missing[7:]}",
                                      flag="video audio unavailable, will retry")
            return
        hit = None
        if self._lossless_allowed(req):
            if req.lossless_retry:
                self.store.update_request(req.id, lossless_retry=0)   # one pass, spent now
            hit = await self._try_lossless(req, cand, catalog, acoustic)
        if (hit is not None and hit.reference.from_query and catalog is not None
                and cand.source in (CATALOG_SOURCE, QUERY_SOURCE)):
            # No record vouches for this file: Beatport's spelling found nothing the recording check
            # accepted and the owner's own words did. The Beatport record is not this recording, so it must
            # not name the file either. (With a chosen Deezer record the record's tags stand, which is why
            # the stand-in candidates are the only sources this applies to.)
            log.info("req#%d: filing on the request's words; the Beatport match was a different recording", req.id)
            catalog = None
            cand = query_candidate(req.query(), req.id)
            self.store.update_request(req.id, catalog_track_id=None)
        if hit is None and cand.source in (CATALOG_SOURCE, QUERY_SOURCE):
            # Neither stand-in has a source_ref the bot would recognise -- a Beatport id it has never heard
            # of, or the request's own words -- so there is no Deezer copy to fall back to. The providers
            # were the only route and this pass produced no file. This returns before the fetch below,
            # which is what spec §7's "no lossy fallback on that path" means in code.
            await self._no_route(req)
            return
        if hit is None:
            self.store.update_request(req.id, fetch_source=self.source.name)
            try:
                # A folder of this request's own, not the shared tmp dir. A source names the file after
                # the track (the Deezer bot uses the Deezer id), so two requests for the same track --
                # a duplicate paste, the same remix in two playlists -- used to be handed the same path.
                # Serially that was fine; running at once, one request's cleanup deletes the other's file
                # out from under ffmpeg. Scoping the folder fixes it for every source at once, rather
                # than asking each of them to remember to make its own names unique.
                fetch_dir = self.settings.tmp_dir / f"req{req.id}"
                tmp = await self.source.fetch(cand, fetch_dir)
            except SourceUnauthorized:
                self.store.update_request(req.id, fetch_source=None)
                raise
            except (SourceTimeout, SourceError) as e:
                self.store.update_request(req.id, fetch_source=None)
                await self._retry_or_fail(req, f"source error: {e}")
                return
        else:
            tmp = hit.path
        try:
            await self._verify_and_file(req, cand, catalog, tmp, hit, acoustic)
        finally:
            tmp.unlink(missing_ok=True)  # gone already when file_track moved it; garbage in every other outcome
            shutil.rmtree(self.settings.tmp_dir / f"req{req.id}", ignore_errors=True)  # spec §13: leave tmp_dir empty
            self.store.update_request(req.id, fetch_source=None)
