"""Offering a filed track's extended mix (issue #187).

With "Offer extended mixes" on, a track downloads exactly as it always has. Once it is filed, and only while
nothing else is in the queue, Soulseek is searched for its extended or club mix -- searched, not downloaded.
A copy that is clearly longer than the filed one becomes an offer on the track's Done row; the download and
the fingerprint happen only when the owner presses Get extended, and the extended file then takes the
filed one's place the way Find lossless swaps a file, so the library never holds both."""
from __future__ import annotations

import asyncio
import logging

from ..lossless import downloaded_mix, extended_reference, pick, policy_from_settings, search_text
from ..models import Request
from ..source import LosslessError, LosslessUnavailable
from ..version import EXTENDED, extended_mix_name, wants_extended

log = logging.getLogger(__name__)

OFFER = "extended_offer"
# The words the offer searches with, in order: a club mix upload often never says extended.
OFFER_WORDS = ("extended", "club mix")
# How much longer than the filed track a copy has to advertise to be offered as its extended mix. A "club
# mix" shorter than the radio edit turned up in the real-pair run (209 s for 233 s, 2026-10-23); an edit a
# few seconds longer is no extended mix either.
LONGER_BY_S = 30
# How long the scan waits before asking again after Soulseek was not reachable.
SCAN_BACKOFF_S = 60.0


class ExtendedMixin:
    """Part of `Worker`, which supplies the state these methods read."""

    def _offers_extended(self, req: Request, mix_name: str | None) -> bool:
        """A track filed now gets an offer looked for: the owner wants offers, Soulseek is set up, and nothing
        in the request or the record rules an extended mix out (one already, a remaster)."""
        return (self.settings.offer_extended and bool(self.providers) and self.settings.lossless_enabled
                and wants_extended(req.query_version) and wants_extended(mix_name))

    def _start_extended_scan(self) -> None:
        """On the loop's tick: one pending track at a time, and only with the queue empty, so the searches
        never stand in line with a download that someone is waiting for."""
        if self._tasks or (self._scan is not None and not self._scan.done()):
            return
        if not self.settings.offer_extended or not self.providers or self.clock() < self._scan_after:
            return
        ids = self.store.tracks_with_evidence(OFFER, "pending", limit=1)
        if ids:
            self._scan = asyncio.create_task(self._scan_extended(ids[0]), name=f"extended{ids[0]}")

    async def _scan_extended(self, track_id: int) -> None:
        try:
            offer = await self._find_extended(track_id)
        except asyncio.CancelledError:
            raise
        except LosslessUnavailable:
            offer = None
        except Exception:  # a background nicety: never out of here, and never asked about again
            log.exception("track#%d: the extended-mix search failed", track_id)
            offer = {"status": "none"}
        if offer is None:       # Soulseek was not there to ask; the track stays pending
            self._scan_after = self.clock() + SCAN_BACKOFF_S
            return
        try:
            self.store.set_evidence(track_id, OFFER, offer)
        except Exception:
            log.exception("track#%d: could not record the extended-mix search", track_id)

    async def _find_extended(self, track_id: int) -> dict | None:
        """The offer for one filed track: its extended mix's advertised length and name, or that there is
        none. None when Soulseek could not be asked. Stops at the first spelling with a longer copy."""
        t = self.store.get_track(track_id)
        if t.duration_s is None:
            return {"status": "none"}       # nothing to be longer than
        try:
            req, cand, catalog = self._filed_request(t)
        except ValueError:
            return {"status": "none"}
        base = self._search_references(req, cand, catalog)[0]
        policy = policy_from_settings(self.settings)
        for word in OFFER_WORDS:
            ref = extended_reference(base, word)
            for provider in self.providers:
                if (await provider.health()).get("status") != "ok":
                    return None
                try:
                    files = await provider.search(search_text(ref), wait_s=self.settings.lossless_search_wait_s)
                except LosslessUnavailable:
                    return None
                except LosslessError as e:
                    log.info("track#%d: extended search on %s failed: %s", track_id, provider.name, e)
                    continue
                longer = [f for f in pick(files, ref, policy).survivors
                          if f.length_s is not None and f.length_s >= t.duration_s + LONGER_BY_S]
                if longer:
                    f = longer[0]
                    return {"status": "available", "length_s": f.length_s,
                            "mix_name": downloaded_mix(f, ref) or extended_mix_name(base.mix_name)}
        return {"status": "none"}

    async def get_extended(self, track_id: int) -> str:
        """Get extended: download the extended mix on offer, fingerprint it against the track's own
        reference, and put it in the filed track's place. Returns a sentence for the owner either way."""
        t = self.store.get_track(track_id)
        offer = self.store.evidence_of(track_id, OFFER)
        if not offer or offer.get("status") != "available":
            raise ValueError("there is no extended mix on offer for this track")
        if not self.providers or not self.settings.lossless_enabled:
            raise ValueError("Soulseek isn't set up, so there is nothing to download it from")
        # Two windows, or a second press that went straight to the API, must not swap the same file twice
        if track_id in self._extending:
            raise ValueError("the extended mix of this track is already being fetched")
        self._extending.add(track_id)
        try:
            return await self._get_extended(t)
        finally:
            self._extending.discard(track_id)

    async def _get_extended(self, t) -> str:
        req, cand, catalog = self._filed_request(t)
        verdict = self.store.get_lossless_verdict(req.id)
        try:
            acoustic = await self._acoustic_reference(req, cand)
            hit = await self._try_lossless(req, cand, catalog, acoustic, prefer_extended=True, extended_only=True)
        except LosslessUnavailable:
            hit = None
        if hit is None:
            # The track is still the one it was: its row keeps the verdict of the pass that filed it
            self.store.set_lossless_verdict(req.id, verdict)
            return "The extended mix could not be downloaded this time. Try again later."
        try:
            if hit.cut != EXTENDED:
                return "The copy that came was not the extended mix, so nothing was replaced."
            return await self._replace_with(t, req, cand, catalog, hit)
        finally:
            hit.path.unlink(missing_ok=True)

    def keep_this(self, track_id: int) -> None:
        """Keep this: the owner keeps the version filed, and the offer goes away."""
        offer = self.store.evidence_of(track_id, OFFER)
        if not offer or offer.get("status") != "available":
            raise ValueError("there is no extended mix on offer for this track")
        self.store.set_evidence(track_id, OFFER, {**offer, "status": "kept"})
