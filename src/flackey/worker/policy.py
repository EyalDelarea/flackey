"""The worker's retry ladder and which lossless outcomes lead where. Numbers and sets only."""
from __future__ import annotations

from ..models import RequestState

MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = (30, 120)  # wait before attempt 2, before attempt 3
REVIEW_BUTTONS = 5
# IDENTIFYING and FETCHING are here because a download in progress is exactly the thing an owner watching a
# slow transfer wants to stop. VERIFYING and FILING deliberately are not: those stages move a file into the
# library, and cancelling mid-move is the one outcome that does not clean itself up. They are seconds long,
# so the answer to "stop it" there is that it has already stopped.
CANCELLABLE = {RequestState.QUEUED, RequestState.IDENTIFYING, RequestState.FETCHING,
               RequestState.AWAITING_REVIEW, RequestState.ERROR}
LOGIN_REQUIRED = "Telegram login required"
# Which rung of the ladder a request is on, for the one failure state that does not say so by its own name
# (issue #60). Written on the row when it turns to `error`, and read by the Failed tab to split one
# undifferentiated lump into Search / Download / Verify. Filing sits under "verify" because it is the back
# half of the same rung on screen: the file has been checked and is being put away. The terminal states
# are deliberately absent -- a request never fails *out of* one -- and so is `error` itself.
STAGE_OF_STATE = {
    RequestState.QUEUED: "search", RequestState.IDENTIFYING: "search",
    RequestState.AWAITING_REVIEW: "choose",
    RequestState.FETCHING: "download",
    RequestState.VERIFYING: "verify", RequestState.FILING: "verify",
}
# spec §5: only these let a request try again on its own. "queued" belongs with them because it is a wait,
# not an answer -- the peers had the file and simply had not reached us in their queue, so the next pass is
# asking a question that has genuinely changed. Everything else is a verdict the next pass would only repeat.
RETRY_LOSSLESS_OUTCOMES = {"unavailable", "interrupted", "queued"}
# Whether a *later pass* may search a provider again, which is a different question: a request that fell
# back to the lossy copy is DONE and nothing reprocesses it, so "may search again" never means "will come
# back on its own". `no_pick` sits here and not above for exactly that reason. It belongs here at all
# because Soulseek is a population, not a library -- the same query for "Space Dwarfs" found no survivor
# at 11:40 on 2026-09-10 and two, one on a free slot, at 14:51.
SEARCH_AGAIN_AFTER = RETRY_LOSSLESS_OUTCOMES | {"no_pick"}
# Outcomes that say nothing about the *next* peer, so the attempt moves on to the next ranked
# survivor. Four kinds sit here: the file was wrong (verify, fingerprint), the peer would not
# send it (rejected the transfer, or never started after leaving the queue), the peer stopped
# part-way (transfer_timeout), and the peer is simply busy (queued). All four are facts about one
# peer, never about the file, so giving up on the whole attempt at any of them threw away
# survivors that were still good. transfer_timeout used to be excluded on the theory that a slow
# transfer measures the link rather than the peer; the Filteria measurement on 2026-09-10 says
# otherwise -- 102 kB/s from one peer while four survivors sat on free slots with nobody queued.
# Not here: unavailable/interrupted, which the request-level retry (RETRY_LOSSLESS_OUTCOMES)
# already handles, and no_pick, which is about the search rather than any peer.
SECOND_PICK_AFTER = {"verify_failed", "fingerprint_failed", "transfer_failed", "first_byte_timeout", "queued",
                     "transfer_timeout"}
# After these, the next spelling gets a search of its own (issue #69). Both say this spelling found nothing
# that is the recording; neither says anything about the other spellings. Everything else is either a fact
# about the provider (unavailable, interrupted) or a fact that holds for every spelling alike
# (fingerprint_unavailable: there is no reference to check against), so typing different words changes
# nothing and the request stops here.
SECOND_SEARCH_AFTER = {"no_pick", "fingerprint_failed"}
# A Soulseek queue moves in minutes to hours, so the 30 s/120 s ladder would ask again before anything could
# possibly have changed. Long enough to be a real second look, short enough that the row is not abandoned.
SLOW_BACKOFF_S = 900
# Outcomes whose answer can only change on the timescale of a Soulseek queue moving or the people online
# turning over. The 30 s/120 s ladder would ask again long before either could happen.
SLOW_RETRY_OUTCOMES = {"queued", "no_pick"}
# Outcomes whose answer changes only as the people online turn over. Soulseek is a population, not a
# library: the search for "Space Dwarfs" found nothing at 11:40 on 2026-09-10 and two copies at 14:51.
# After the quick ladder (RETRY_BACKOFF_S) a request that ended in one of these waits LONG_RETRY_EVERY_S in
# `queued` and looks again, LONG_RETRY_TIMES times, before it parks in `error`. `fingerprint_failed` is
# here although it is a verdict: every copy offered was a different recording, which says nothing about
# the copies tomorrow's peers will offer. Not here: a missing reference or nothing to search for, which no
# amount of waiting changes.
LONG_RETRY_OUTCOMES = {"no_pick", "fingerprint_failed", "transfer_failed", "first_byte_timeout",
                       "transfer_timeout", "queued", "unavailable", "interrupted"}
LONG_RETRY_EVERY_S = 6 * 3600
LONG_RETRY_TIMES = 12            # 3 days at 6 h
MAINTENANCE_EVERY_S = 86_400
LOSSLESS_HEALTH_EVERY_S = 60        # once the provider answers "ok"
LOSSLESS_HEALTH_SETTLING_S = 5      # while it is still connecting, or has gone away
