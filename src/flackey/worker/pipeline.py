"""Identify a request, choose its record, and hand it to fetch, verify and file."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..export import write_new_playlist, write_playlist
from ..identify import parse_text
from ..library import clear_request_tmp, find_duplicate, find_extended
from ..match import candidate_query, candidate_version, decide, identification_order, same_version
from ..models import Candidate, CatalogTrack, Query, Request, RequestState, Track
from ..notify import Button
from ..reference import Identification, identify_record
from ..source import SourceError, SourceNotFound, SourceTimeout, SourceUnauthorized
from ..version import EXTENDED
from . import usage
from .policy import MAX_ATTEMPTS, REVIEW_BUTTONS, bot_paused
from .records import QUERY_SOURCE, _mmss, query_candidate

log = logging.getLogger(__name__)


class PipelineMixin:
    """Part of `Worker`, which supplies the state these methods read."""

    async def _process(self, request_id: int) -> None:
        self._bot_skipped.discard(request_id)   # a fact about one pass; this is a new one
        req = self.store.get_request(request_id)
        query = req.query()
        if query.artist is None and query.title is None:
            query = parse_text(req.raw_text)
            self.store.update_request(req.id, query_artist=query.artist, query_title=query.title,
                                      query_version=query.version)
            req = self.store.get_request(request_id)   # later stages read the asked version off the row

        if req.chosen_candidate_id is not None:
            cand = self.store.get_candidate(req.chosen_candidate_id)
            catalog = self.store.get_catalog_track(req.catalog_track_id) if req.catalog_track_id else None
        else:
            picked = await self._identify(req, query)
            if picked is None:
                return
            cand, catalog = picked
        catalog = await self._catalog_for(req, cand, catalog)
        await self._fetch_verify_file(req, cand, catalog)

    async def _identify(self, req: Request, query: Query) -> tuple[Candidate, CatalogTrack | None] | None:
        """Search the source, choose the record, and persist the choice. Answers with the candidate to
        fetch (and no catalog record: tags are filled at filing, from Deezer and Discogs), or None when this
        pass has already been settled here -- retried, parked for review, a duplicate, or handed to the
        search on the request's own words.

        There is no catalogue search before the download any more. Beatport was one, until its Cloudflare
        challenge refused every lookup from 2026-10-14; it is gone, and the version, the duplicate check
        and the pick all run on the request and the source's own candidates, as they did whenever Beatport
        did not know a track (issue #69)."""
        self._set_state(req, RequestState.IDENTIFYING)
        catalog: CatalogTrack | None = None

        cands: list[Candidate] = []
        # What to tell the owner if nothing identifies the track. "No Deezer candidates" covers four
        # different situations and the row used to say which, so it still does: a bot that is switched
        # off is the owner's own setting, a paused one is Telegram's and goes away with a sign-in, and the
        # bot's own sentence is how a not-found is diagnosed.
        source_why = "the Deezer bot is switched off"
        if self.settings.source_enabled and not self._bot_ready():
            # Never asked, not even to time out: a signed-out client either raises at once or, with no
            # keys at all, is not connected to anything. The pass carries on as if the bot were off.
            source_why = bot_paused(self.settings.telegram_configured)
            self._bot_skipped.add(req.id)
        elif self.settings.source_enabled:
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
                usage.failure_reason(req.id, usage.reason_of(e))
                # The source may be back in an hour and would offer a lossy fallback the query-only
                # path does not have; a retry is worth more than a Soulseek-or-nothing pass now. (A
                # Beatport match used to send this pass to Soulseek instead; there is none any more.)
                await self._retry_or_fail(req, f"source error: {e}")
                return None

        if not cands:
            # Either the source is switched off or it found nothing: the request's own words are left.
            await self._search_on_the_request(req, query, None, source_why)
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
            # Only the asked version's previews: the fingerprint matches a remaster's preview and its
            # original's alike, so trying the wrong one first filed it (issue #172)
            ordered = identification_order(query, cands)
            if ordered:
                async with self._cpu:
                    ident = await identify_record(video, ordered, self.http, self.settings.tmp_dir,
                                                  minimum=self.settings.lossless_fingerprint_min)
            else:
                ident = Identification(None, None, [], f"no Deezer record is the requested version "
                                                       f"'{query.version}'")
            log.info("req#%d identification: %s (tried %s)", req.id, ident.reason, ident.tried)
        self._interpret(req, video, ident)   # labels the request in the background; changes nothing here
        chosen_obj = ident.chosen if ident is not None else decision.chosen
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

        dup, _ = self._owned(req, catalog, chosen)
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
        """The stored record, while it describes the version being downloaded. A request identified before
        Beatport was removed can still carry a Beatport record, matched for the video's title, i.e. for the
        original. When the candidate is another version (the owner picked a remix, or the video's length
        pinned an edit) that record is dropped rather than re-matched: filing then tags the file from the
        candidate itself, filled from Deezer and Discogs (`_tags_for`), so the tags describe the recording
        in the file."""
        if catalog is None or same_version(cand, catalog, req.query_version):
            return catalog
        log.info("req#%d: the stored record is the %s, not the %s being fetched; tagging from the candidate",
                 req.id, catalog.mix_name, candidate_version(cand))
        self.store.update_request(req.id, catalog_track_id=None)
        return None


    async def _search_on_the_request(self, req: Request, query: Query, catalog: CatalogTrack | None,
                                     why: str) -> None:
        """No record was chosen. Soulseek is the only network that has this library's music, and the
        fingerprint is what proves the file; the owner's own words are enough to ask with (issue #69)."""
        if not (query.artist and query.title):
            usage.failure_reason(req.id, "unidentified")
            self._set_state(req, RequestState.NOT_FOUND, error_message=f"could not identify this track: {why}; "
                            f"no artist and title could be read from the request, so there is nothing to search for")
            await self.notifier.send(f"Could not identify: {req.raw_text}")
            return
        if not self._lossless_allowed(req):
            await self._no_route(req)
            return
        await self._fetch_verify_file(req, query_candidate(query, req.id), catalog)


    def _owned(self, req: Request, catalog: CatalogTrack | None, cand: Candidate) -> tuple[Track | None, Track | None]:
        """The library track this request would duplicate, and the one it may go on past. With the owner
        preferring extended mixes, the radio edit already filed is not the track: only an extended copy is
        a duplicate, and the normal one comes back second, so the pass looks for the extended mix alone
        and stops at what is already there when there is none (issue #187)."""
        dup = find_duplicate(self.store, catalog, cand, req.query_version, library_root=self.settings.library_root)
        mix = catalog.mix_name if catalog else candidate_version(cand)
        if not self._prefers_extended(req, mix):
            return dup, None
        artist, title = (catalog.artist, catalog.title) if catalog else (cand.artist, candidate_query(cand).title)
        extended = find_extended(self.store, artist, title, mix, library_root=self.settings.library_root)
        return (extended, None) if extended else (None, dup)

    def _export_playlist(self, playlist_id: int) -> None:
        """Rewrite a playlist's M3U8 after its membership changed. Best effort: by now the track is in the
        library and in the playlist, and a file another app holds open (Rekordbox on Windows) must not turn
        that into a failed request (ENG-4). The next change to the playlist writes it again."""
        try:
            write_playlist(self.store, playlist_id, self.settings.library_root)
        except OSError as e:
            log.warning("could not write the file for playlist #%d: %s", playlist_id, e)

    def _export_new(self) -> None:
        """Rewrite the "Flackey - New" playlist after a file landed in the library. Best effort, for the
        same reason as `_export_playlist`."""
        try:
            write_new_playlist(self.store, self.settings.library_root)
        except OSError as e:
            log.warning("could not write the New playlist: %s", e)

    async def _mark_duplicate(self, req: Request, track_id: int | None, path: Path) -> None:
        self._set_state(req, RequestState.DUPLICATE, track_id=track_id)
        if req.playlist_id is not None and track_id is not None:
            self.store.add_playlist_track(req.playlist_id, track_id, req.playlist_position or 0)
            self._export_playlist(req.playlist_id)   # ENG-2: a duplicate joins the M3U8 like a filed track
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
        try:
            await self._fetch_verify_file_once(req, cand, catalog)
        finally:
            # Whatever ended the pass -- a Stop mid-verify, a bot timeout, a provider error -- nothing this
            # request downloaded stays in tmp_dir (spec §13, ENG-6). The finally further in only covers the
            # verify-and-file step; the downloads before it used to be left behind.
            clear_request_tmp(self.settings.tmp_dir, req.id)

    async def _fetch_verify_file_once(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> None:
        dup, owned = self._owned(req, catalog, cand)  # again: a reviewed request may have waited for hours
        if dup:
            await self._mark_duplicate(req, dup.id, dup.path)
            return
        self._set_state(req, RequestState.FETCHING)
        # Once per request, before anything downloads: the reference is what every pick below, the lossy
        # fallback and any later retry are checked against, and it does not change between them.
        acoustic = await self._acoustic_reference(req, cand)
        if acoustic.reference is None and acoustic.missing.startswith("video: ") and req.attempts + 1 < MAX_ATTEMPTS:
            # The same quick ladder `_process` gives a request whose record is chosen by audio, for the route
            # that has no record at all. Since issue #69 the catalogue-less path is how a track the source has
            # no record of gets filed, and on it the video is the *only* reference: a stand-in candidate has
            # no Deezer preview to fall back on, so a yt-dlp blip used to end the request in `error` on its
            # first pass -- after a real search and a download it then had nothing to check. Only a fetch that
            # failed ("video: "), never audio that arrived unreadable, and only while the ladder has budget:
            # after it the pass goes on and ends `fingerprint_unavailable` as before.
            await self._retry_or_fail(req, f"could not fetch the video's audio: {acoustic.missing[7:]}",
                                      flag="video audio unavailable, will retry")
            return
        # A request the bot had nothing for is compared here, where the video is in hand anyway rather than
        # fetched for the comparison first; one `_identify` already started is not started again.
        video = acoustic.reference if acoustic.reference is not None and acoustic.reference.kind == "youtube" else None
        self._interpret(req, video)
        hit = None
        if self._lossless_allowed(req):
            if req.lossless_retry:
                self.store.update_request(req.id, lossless_retry=0)   # one pass, spent now
            hit = await self._try_lossless(req, cand, catalog, acoustic, extended_only=owned is not None)
        if owned is not None and (hit is None or hit.cut != EXTENDED):
            # The normal version is in the library already and no extended mix of it turned up
            if hit is not None:
                hit.path.unlink(missing_ok=True)       # spec §13: every outcome leaves tmp_dir empty
            self.store.update_request(req.id, fetch_source=None)
            log.info("req#%d: no extended mix found; the normal version is already in the library", req.id)
            await self._mark_duplicate(req, owned.id, owned.path)
            return
        if hit is None and cand.source != QUERY_SOURCE and not self._bot_ready():
            # A record the bot offered on an earlier pass -- picked in review, or kept when a fetch was cut
            # short -- and the bot is not there to send it now: switched off since, or Telegram signed out.
            # Fetching anyway would only raise, so this pass ends the way a stand-in's does, below.
            if self.settings.source_enabled:
                self._bot_skipped.add(req.id)
            await self._no_route(req)
            return
        if hit is not None and hit.reference.from_query and cand.source == QUERY_SOURCE:
            reading = self._reading_that_found(req, hit.reference)
            if reading is not None:
                # Another reading of the title found it, at the end of the spelling ladder. Those are the
                # words a peer's file name and the fingerprint agree on; the request's own found nothing.
                log.info("req#%d: filing on another reading of the title, %s - %s", req.id, reading.artist,
                         reading.title)
                cand = query_candidate(reading, req.id)
        if hit is None and cand.source == QUERY_SOURCE:
            # The stand-in has no source_ref the bot would recognise -- it is the request's own words -- so
            # there is no Deezer copy to fall back to. The providers
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
                # Noted here as the search's failure is above: without it a give-up reads its reason off
                # the Soulseek attempt, and a bot that timed out was sent as `peer_queue_timeout`.
                usage.failure_reason(req.id, usage.reason_of(e))
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
