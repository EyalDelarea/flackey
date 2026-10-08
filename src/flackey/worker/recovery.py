"""What a pass that did not file anything does next: the quick ladder, the long wait for
Soulseek, or a terminal state, and the sentence that tells the owner which."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from ..models import MISS_REASON, Request, RequestState
from .policy import (
    LONG_RETRY_EVERY_S,
    LONG_RETRY_OUTCOMES,
    LONG_RETRY_TIMES,
    MAX_ATTEMPTS,
    RETRY_BACKOFF_S,
    RETRY_LOSSLESS_OUTCOMES,
    SEARCH_AGAIN_AFTER,
    SLOW_BACKOFF_S,
    SLOW_RETRY_OUTCOMES,
)

log = logging.getLogger(__name__)


class RecoveryMixin:
    """Part of `Worker`, which supplies the state these methods read."""

    async def _retry_or_fail(self, req: Request, reason: str, *, flag: str | None = None,
                             wait_s: int | None = None, long_after: str | None = None) -> None:
        """The quick ladder (RETRY_BACKOFF_S) while attempts < MAX_ATTEMPTS, then `error` -- or, when
        `long_after` names why the answer may change, the long wait for Soulseek first."""
        attempts = req.attempts + 1
        if attempts < MAX_ATTEMPTS:
            wait = RETRY_BACKOFF_S[min(attempts, len(RETRY_BACKOFF_S)) - 1] if wait_s is None else wait_s
            retry_after = (datetime.now(UTC) + timedelta(seconds=wait)).isoformat(timespec="seconds")
            self._set_state(req, RequestState.QUEUED, attempts=attempts, flag_reason=flag or reason,
                            retry_after=retry_after)
            await self.notifier.send(f"Attempt {attempts} failed for {req.raw_text}: {reason}\nRetrying in {wait} s.")
        elif long_after:
            await self._wait_for_soulseek(req, long_after, attempts)
        else:
            self._set_state(req, RequestState.ERROR, attempts=attempts, error_message=reason)
            await self.notifier.send(f"Gave up after {attempts} attempts: {req.raw_text}\n{reason}")


    async def _wait_for_soulseek(self, req: Request, why: str, attempts: int) -> None:
        """Park the request in `queued` for LONG_RETRY_EVERY_S with `lossless_retry` granted, so the next
        pass really searches whatever the last outcome was (`_lossless_allowed`); or in `error` once the
        LONG_RETRY_TIMES looks are spent. Told to the owner once, when the quick ladder hands over; the row
        shows the countdown and the reason meanwhile. `attempts` keeps counting past MAX_ATTEMPTS so the
        budget survives a restart: it lives on the row, not in memory."""
        hours = LONG_RETRY_EVERY_S // 3600
        if attempts >= MAX_ATTEMPTS + LONG_RETRY_TIMES:
            reason = (f"{why}; looked {LONG_RETRY_TIMES} times over {LONG_RETRY_TIMES * hours} h. "
                      f"Use Try again in the app to search once more")
            self._set_state(req, RequestState.ERROR, attempts=attempts, error_message=reason)
            await self.notifier.send(f"Gave up: {req.raw_text}\n{reason}")
            return
        retry_after = (datetime.now(UTC) + timedelta(seconds=LONG_RETRY_EVERY_S)).isoformat(timespec="seconds")
        left = MAX_ATTEMPTS + LONG_RETRY_TIMES - attempts
        self._set_state(req, RequestState.QUEUED, attempts=attempts, retry_after=retry_after, lossless_retry=1,
                        flag_reason=f"waiting for Soulseek: {why}; {left} more "
                                    f"look{'' if left == 1 else 's'}, one every {hours} h")
        if attempts <= MAX_ATTEMPTS:
            await self.notifier.send(f"Waiting for Soulseek: {req.raw_text}\n{why}. Looking again every {hours} h "
                                     f"for {LONG_RETRY_TIMES * hours // 24} days.")


    async def _no_route(self, req: Request) -> None:
        """Soulseek was the only way to a file and this pass did not produce one. Two things have to be
        right here, and both used to be wrong.

        What to say. The old line asserted that Soulseek "has already looked and found nothing" whatever had
        actually happened, which for the live failure this was written from was untrue three ways over: the
        peers had five perfect copies and were merely busy. The attempt row knows which outcome it really was
        and `MISS_REASON` already words each one, so read it rather than guess. Likewise the source is
        "switched off" when the owner switched it off, and only otherwise "unavailable".

        Whether to come back. Exactly `_lossless_allowed`: after a retryable outcome the next pass really does
        search again, and after a definitive one it would repeat this pass's work to reach this pass's answer
        -- three passes, two "retrying" messages, no searches. Say it once and stop, the same way a track
        neither side can identify lands terminally rather than burning the backoff schedule."""
        attempt = self.store.get_attempt_for_request(req.id)
        outcome = attempt.outcome if attempt else None
        why = MISS_REASON.get(outcome) if outcome else None
        if why is None:
            why = ("Soulseek is switched off" if not (self.providers and self.settings.lossless_enabled)
                   else "Soulseek found no copy of it")
        fallback = ("the Deezer bot is switched off, so there is no lossy copy to fall back on"
                    if not self.settings.source_enabled else "Deezer offered nothing to fall back on")
        reason = f"no way to fetch this track: {why}, {fallback}"
        transient = outcome in LONG_RETRY_OUTCOMES
        if self._lossless_allowed(self.store.get_request(req.id)):
            await self._retry_or_fail(req, reason, wait_s=SLOW_BACKOFF_S if outcome in SLOW_RETRY_OUTCOMES else None,
                                      long_after=why if transient else None)
            return
        if transient:
            # A verdict the next minute would only repeat (every copy was a different recording, every peer
            # refused): no quick ladder, straight to the wait for the people online to change.
            await self._wait_for_soulseek(req, why, max(req.attempts + 1, MAX_ATTEMPTS))
            return
        # "Try again" is the right advice for a miss a later search could answer differently -- but not for
        # one that cannot be checked at all: with no reference, the next search reaches the same dead end.
        reason += (". Use Try again once it can be checked" if outcome == "fingerprint_unavailable"
                   else ". Use Try again in the app to search once more")
        self._set_state(req, RequestState.ERROR, attempts=req.attempts + 1, error_message=reason)
        await self.notifier.send(f"Could not fetch: {req.raw_text}\n{reason}")


    def _lossless_allowed(self, req: Request) -> bool:
        if not self.providers or not self.settings.lossless_enabled:
            return False
        last = self.store.get_attempt_for_request(req.id)
        # Spec §5 stops the *worker* going back to a provider on its own after a definitive miss. It does
        # not bind the owner: `retry()` grants `lossless_retry` on a failed request, which is the button
        # `_lossless_miss_line` points them at. `upgrade()` skips this check outright for the same reason.
        return last is None or last.outcome in SEARCH_AGAIN_AFTER or bool(req.lossless_retry)


    def _lossless_miss_line(self, req: Request) -> str | None:
        """The owner asked to be told, not just shown, when a track lands on the lossy copy. Only when a
        lossless attempt actually ran and missed: with the provider off or absent there is nothing to
        report, and saying "no lossless copy" then would blame Soulseek for a setting.

        The outcome is the whole test. A successful hit always finishes its attempt as "filed" before
        `_try_lossless` returns it, so an extra `hit is None` guard at the call site would be a branch no
        input can reach - and this way the line stays right however the file was obtained."""
        attempt = self.store.get_attempt_for_request(req.id)
        if attempt is None or attempt.outcome in (None, "filed"):
            return None
        why = MISS_REASON.get(attempt.outcome, f"the lossless attempt ended in {attempt.outcome}")
        tail = ("It will try again on its own." if attempt.outcome in RETRY_LOSSLESS_OUTCOMES
                else "Use Try again in the app to search Soulseek once more.")
        return f"No lossless copy this time — {why}. {tail}"
