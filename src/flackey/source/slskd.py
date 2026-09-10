"""slskd REST adapter (spec §7): a thin HTTP client and the first LosslessProvider. Facts about the API that
the spike established are in the tests' docstring and in the spec; nothing here is guessed."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx

from ..lossless import LosslessFile
from .lossless import LosslessError, LosslessUnavailable, RawSink, TransferProgress

log = logging.getLogger(__name__)
SEARCH_IDLE_MS = 5000       # slskd's own "no new responses for this long" timeout; milliseconds
RESPONSE_LIMIT = 100
SEARCH_POLL_S = 0.5
# slskd's transfer states while nothing has been sent yet and the peer has not refused. "Requested" is the
# moment before the peer answers; the two "Queued" states are its answer -- we are in a line, behind however
# many other people the peer is serving (127, 360 and 1866 deep on the goa FLACs this was written for).
QUEUED_STATES = ("Requested", "Queued")


def is_queued(state: str) -> bool:
    return state.startswith(QUEUED_STATES)


class SlskdClient:
    def __init__(self, base_url: str, api_key: str, http: httpx.AsyncClient, *,
                 sleep=asyncio.sleep, clock: Callable[[], float] = time.monotonic):
        self.base = base_url.rstrip("/") + "/api/v0"
        self._headers = {"X-API-Key": api_key}
        self.http, self.sleep, self.clock = http, sleep, clock

    async def _call(self, method: str, path: str, *, timeout: float = 30, **kw) -> httpx.Response:
        try:
            r = await self.http.request(method, self.base + path, headers=self._headers, timeout=timeout, **kw)
        except httpx.HTTPError as e:
            raise LosslessUnavailable(f"slskd unreachable: {type(e).__name__}") from e
        if r.status_code >= 500:
            raise LosslessUnavailable(f"slskd http {r.status_code}")
        return r

    async def application(self) -> dict:
        r = await self._call("GET", "/application", timeout=5)
        return r.json()

    async def start_search(self, text: str, *, deadline: float) -> str:
        body = {"searchText": text, "searchTimeout": SEARCH_IDLE_MS, "responseLimit": RESPONSE_LIMIT}
        while True:
            r = await self._call("POST", "/searches", json=body)
            if r.status_code == 429 and self.clock() < deadline:
                await self.sleep(1)      # another search is being created; the wait shares the search budget
                continue
            if r.status_code >= 400:
                raise LosslessError(f"search rejected: http {r.status_code}", "no_pick")
            return r.json()["id"]

    async def search_state(self, sid: str) -> dict:
        return (await self._call("GET", f"/searches/{sid}")).json()

    async def search_responses(self, sid: str) -> list[dict]:
        return (await self._call("GET", f"/searches/{sid}/responses")).json()

    async def delete_search(self, sid: str) -> None:
        try:
            await self._call("DELETE", f"/searches/{sid}")
        except LosslessError:
            log.debug("could not delete search %s", sid)

    async def enqueue(self, username: str, filename: str, size: int) -> None:
        r = await self._call("POST", f"/transfers/downloads/{quote(username, safe='')}",
                             json=[{"filename": filename, "size": size}])
        if r.status_code >= 400:
            raise LosslessError(f"enqueue rejected: http {r.status_code} {r.text[:120]}", "transfer_failed")

    async def downloads(self, username: str | None = None) -> list[dict]:
        """Every download file object slskd knows, flattened (for one user when given)."""
        r = await self._call("GET", "/transfers/downloads" + (f"/{quote(username, safe='')}" if username else ""))
        users = r.json()
        if isinstance(users, dict):
            users = [users]
        return [f for u in users for d in u.get("directories", []) for f in d.get("files", [])]

    async def uploads(self) -> list[dict]:
        """Every upload file object slskd knows, flattened: what peers are pulling from the shared library.
        Same envelope as /transfers/downloads - a list of users, each with directories, each with files."""
        r = await self._call("GET", "/transfers/uploads")
        users = r.json()
        if isinstance(users, dict):
            users = [users]
        return [{**f, "username": f.get("username") or u.get("username")}
                for u in users for d in u.get("directories", []) for f in d.get("files", [])]

    async def cancel_download(self, username: str, transfer_id: str) -> None:
        try:
            await self._call("DELETE", f"/transfers/downloads/{quote(username, safe='')}/{transfer_id}",
                             params={"remove": "true"})
        except LosslessError:
            log.debug("could not cancel transfer %s of %s", transfer_id, username)

    async def rescan_shares(self) -> None:
        await self._call("PUT", "/shares")


def parse_response(resp: dict) -> list[LosslessFile]:
    out = []
    for f in resp.get("files", []):
        name = f["filename"]
        ext = (f.get("extension") or (name.rsplit(".", 1)[-1] if "." in name else "")).lower()
        out.append(LosslessFile(
            provider="soulseek", username=resp["username"], path=name, extension=ext, size=int(f["size"]),
            length_s=f.get("length"), bitrate_kbps=f.get("bitRate"), sample_rate=f.get("sampleRate"),
            bit_depth=f.get("bitDepth"), has_free_slot=bool(resp.get("hasFreeUploadSlot")),
            upload_speed_bps=int(resp.get("uploadSpeed") or 0), queue_length=int(resp.get("queueLength") or 0)))
    return out


def local_path_for(downloads: Path, file: LosslessFile) -> Path:
    """Where slskd writes a completed file: <downloads>/<last remote folder segment>/<file name>. The result must
    resolve inside the downloads folder; the peer chose both strings (spec §7, §16.2)."""
    folder = file.folder.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    candidate = downloads / folder / file.name if folder else downloads / file.name
    root, resolved = downloads.resolve(), candidate.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise LosslessError(f"peer path escapes the downloads folder: {file.path!r}", "transfer_failed")
    return resolved


def _claim(dest: Path) -> Path:
    """Move a finished download off the derived path, under a name no other download can derive.

    Called with the path's lock still held. Without it the caller would own a file at a path the *next*
    download of the same track is entitled to clear away as stale, and the only thing keeping that safe
    would be the absence of an `await` between here and the move into `tmp_dir` -- an invariant nothing
    states and one added await breaks. A failure to rename is not fatal: the file is there and the caller
    is about to move it anyway; only the path's privacy is lost, which is what this had before.
    """
    claimed = dest.with_name(f"{uuid4().hex[:8]}-{dest.name}")
    try:
        dest.replace(claimed)
    except OSError as e:                       # peer-chosen names: ENAMETOOLONG and friends are input, not bugs
        log.warning("could not claim %s off the derived path: %s", dest.name, e)
        return dest
    return claimed


def _remove_stale_file(dest: Path) -> None:
    """Clear whatever sits at the derived path before enqueueing, so slskd cannot dedup a fresh download onto a
    different (.NET-ticks-suffixed) name because the derived name was already taken. The caller holds that path's
    lock (`SoulseekProvider._path_lock`) for the whole download and takes its finished file off the derived path
    before dropping it (`_claim`), so the file this deletes is never another download's. `dest` is built from peer-chosen strings - treat every failure mode as expected
    input, not a bug."""
    try:
        dest.unlink()
        log.info("removed stale file at derived path: %s", dest.name)
    except FileNotFoundError:
        pass  # nothing there to clean - the common case
    except IsADirectoryError as e:
        raise LosslessError(f"derived path is a directory, not a file: {dest.name!r}", "transfer_failed") from e
    except (OSError, ValueError) as e:
        # OSError covers permissions/ENAMETOOLONG/etc.; ValueError covers a malformed peer-chosen name (e.g. an
        # embedded NUL) reaching the syscall layer - either way, no exception but LosslessError leaves here
        raise LosslessError(f"could not remove stale file at {dest.name!r}: {e}", "transfer_failed") from e


@dataclass
class _PathWaiters:
    """A derived path's lock plus how many downloads hold or want it. See `SoulseekProvider._path_lock`."""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    n: int = 0


class SoulseekProvider:
    name = "soulseek"

    def __init__(self, client: SlskdClient, downloads: Path, *, sleep=asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.client, self.downloads, self.sleep, self.clock = client, downloads, sleep, clock
        # slskd creates one search at a time and answers 429 to a concurrent POST (spike findings). The
        # client retries on 429, but with the whole queue searching at once that turns into every request
        # spending its search budget on retries instead of on the search. One at a time, each with its
        # full budget, is both kinder to the sidecar and faster in the end.
        self._searching = asyncio.Lock()
        # One lock per derived download path. Two peers can offer the same folder and file name, and slskd
        # writes both to the same place; whoever gets there first owns the path from the pre-clean until
        # the file has been moved out. Without this, one download's pre-clean would delete another's
        # in-flight file. Entries are dropped when nothing is waiting, so this cannot grow unboundedly.
        self._paths: dict[Path, asyncio.Lock] = {}

    async def health(self) -> dict:
        try:
            app = await self.client.application()
        except LosslessUnavailable:
            return {"status": "unreachable", "username": None}
        server = app.get("server") or {}
        return {"status": "ok" if server.get("isLoggedIn") else "not_logged_in",
                "username": (app.get("user") or {}).get("username")}

    @asynccontextmanager
    async def _path_lock(self, dest: Path) -> AsyncIterator[None]:
        """Own `dest` for the length of a download. The count is bumped before the first await, so an entry
        is only dropped once nobody holds or wants it -- pruning on `Lock.locked()` alone would discard a
        lock a waiter had already been handed."""
        entry = self._paths.get(dest)
        if entry is None:
            entry = self._paths[dest] = _PathWaiters()
        entry.n += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.n -= 1
            if entry.n == 0:
                self._paths.pop(dest, None)

    async def search(self, text: str, *, wait_s: float, on_raw: RawSink | None = None) -> list[LosslessFile]:
        async with self._searching:
            return await self._search_held(text, wait_s=wait_s, on_raw=on_raw)

    async def _search_held(self, text: str, *, wait_s: float, on_raw: RawSink | None = None) -> list[LosslessFile]:
        # The budget starts when this search does, not when its caller queued up behind another one: a
        # request that waited its turn still gets the full `wait_s` to find peers.
        deadline = self.clock() + wait_s
        sid = await self.client.start_search(text, deadline=deadline)
        try:
            state = await self.client.search_state(sid)
            while "Completed" not in state.get("state", "") and self.clock() < deadline:
                await self.sleep(SEARCH_POLL_S)
                state = await self.client.search_state(sid)
            if on_raw:
                on_raw("search", state)
            responses = await self.client.search_responses(sid)   # empty file lists until Completed (spike)
            if on_raw:
                on_raw("responses", responses)
        finally:
            await self.client.delete_search(sid)
        return [f for r in responses for f in parse_response(r)]

    async def _find(self, file: LosslessFile) -> dict | None:
        for t in await self.client.downloads(file.username):
            if t.get("filename") == file.path:
                return t
        return None

    async def download(self, file: LosslessFile, *, first_byte_s: float, total_s: float, poll_s: float,
                       queue_wait_s: float | None = None, stall_s: float | None = None,
                       on_progress: Callable[[TransferProgress], None] | None = None,
                       on_raw: RawSink | None = None) -> Path:
        """Four separate budgets, because a transfer can stop for four unrelated reasons.

        `queue_wait_s` bounds the time spent in the peer's queue: a popular peer with no free slot parks us
        behind everyone else, which says nothing about whether they will send and cannot be told apart from
        a healthy transfer that has simply not started. It ends the attempt as "queued" -- a wait, retryable
        by the caller -- not as a refusal. The other three measure the transfer itself and only start once it
        leaves the queue; counting queue time against them is what made every busy peer look like a peer that
        would not send.

        `first_byte_s` is how long a transfer that has left the queue may send nothing at all, `stall_s` how
        long it may go without a *new* byte once it has started, and `total_s` the absolute ceiling. Length
        is not evidence: a 67 MB FLAC arriving honestly at 100 kB/s takes eleven minutes, so only the bytes
        stopping says the transfer is dead. Give `total_s` room for the file (see `transfer_ceiling_s`); it
        is there for a peer that trickles forever, which no stall bound can catch.
        """
        dest = local_path_for(self.downloads, file)     # containment before anything is enqueued
        async with self._path_lock(dest):
            return await self._download_held(file, dest, first_byte_s=first_byte_s, total_s=total_s,
                                             poll_s=poll_s, queue_wait_s=queue_wait_s, stall_s=stall_s,
                                             on_progress=on_progress, on_raw=on_raw)

    async def _download_held(self, file: LosslessFile, dest: Path, *, first_byte_s: float, total_s: float,
                             poll_s: float, queue_wait_s: float | None = None, stall_s: float | None = None,
                             on_progress: Callable[[TransferProgress], None] | None = None,
                             on_raw: RawSink | None = None) -> Path:
        _remove_stale_file(dest)                        # so a leftover file can't be mistaken for the fresh one
        await self.client.enqueue(file.username, file.path, file.size)
        queue_wait_s = first_byte_s if queue_wait_s is None else queue_wait_s
        stall_s = first_byte_s if stall_s is None else stall_s
        t0, active_at, first_byte_at, last_state, n = self.clock(), None, None, None, 0
        last_done, moved_at = 0, None
        # Only for the cancel path: the id slskd gave this transfer, once we have seen it. Stopping a
        # download the owner cancelled means telling the sidecar too -- otherwise the bytes keep arriving
        # from the peer long after the row has gone, and the next run's `cancel_all` is what finally
        # notices. There is nothing to cancel before the transfer exists, which is what None means.
        transfer_id: str | None = None
        try:
            while True:
                await self.sleep(poll_s)
                tr = await self._find(file)
                now = self.clock()
                if tr is None:
                    # Never even acknowledged: no queue to be in, so this is the peer, not their popularity.
                    if now - t0 > first_byte_s:
                        raise LosslessError("transfer never appeared in slskd", "first_byte_timeout")
                    continue
                transfer_id = tr.get("id")
                done, state = int(tr.get("bytesTransferred") or 0), str(tr.get("state") or "")
                if active_at is None and (done > 0 or not is_queued(state)):
                    active_at = now
                if done > 0 and first_byte_at is None:
                    first_byte_at = now
                if done > last_done:
                    last_done, moved_at = done, now
                first_byte_ms = None if first_byte_at is None else int((first_byte_at - t0) * 1000)
                if state != last_state:
                    if on_raw:
                        on_raw(f"transfer-{n}", tr)
                    n, last_state = n + 1, state
                if on_progress:
                    on_progress(TransferProgress(state, done, file.size, float(tr.get("averageSpeed") or 0), first_byte_ms))
                if done > file.size:
                    await self.client.cancel_download(file.username, tr["id"])
                    raise LosslessError(f"peer sent {done} bytes for a {file.size} byte file", "transfer_failed")
                if state.startswith("Completed"):
                    if "Succeeded" in state and dest.exists():
                        try:
                            actual = dest.stat().st_size
                        except OSError as e:
                            raise LosslessError(f"could not stat completed file at {dest.name!r}: {e}",
                                                "transfer_failed") from e
                        # Compare against slskd's own byte count for the transfer it just wrote, not the size the
                        # peer advertised in the search response: those two describe different things and need not
                        # agree, and only the former measures the file now on disk.
                        expected = done or file.size
                        if actual != expected:
                            raise LosslessError(
                                f"completed file at derived path is {actual} bytes, expected {expected}",
                                "transfer_failed")
                        return _claim(dest)
                    raise LosslessError(f"transfer ended {state}" if "Succeeded" not in state
                                        else "completed but no file at the derived path", "transfer_failed")
                if active_at is None:
                    if now - t0 > queue_wait_s:
                        await self.client.cancel_download(file.username, tr["id"])
                        raise LosslessError(f"still {state.lower() or 'queued'} after {queue_wait_s:.0f} s", "queued")
                    continue
                if first_byte_at is None and now - active_at > first_byte_s:
                    await self.client.cancel_download(file.username, tr["id"])
                    raise LosslessError(f"no bytes within {first_byte_s:.0f} s", "first_byte_timeout")
                if moved_at is not None and now - moved_at > stall_s:
                    await self.client.cancel_download(file.username, tr["id"])
                    raise LosslessError(f"stopped sending after {done} of {file.size} bytes", "transfer_timeout")
                if now - active_at > total_s:
                    await self.client.cancel_download(file.username, tr["id"])
                    raise LosslessError(f"not finished within {total_s:.0f} s", "transfer_timeout")
        except asyncio.CancelledError:
            # The owner stopped this download. Tell slskd so the peer stops sending, and take the partial
            # file with it: nothing else will ever look at it, and leaving it at the derived path would be
            # the stale file the *next* download of the same track has to clean up. Then re-raise -- a
            # cancellation that is swallowed is a task that never actually stops.
            if transfer_id is not None:
                await self.client.cancel_download(file.username, transfer_id)
            dest.unlink(missing_ok=True)
            raise

    async def uploads(self) -> list[dict]:
        """What peers are pulling from the shared library, newest first (spec-neutral shape the UI renders as
        text). Every string here was chosen by a peer or names a shared file: it is displayed, never acted on."""
        out = []
        for t in await self.client.uploads():
            name = str(t.get("filename") or "")
            parts = name.replace("\\", "/").rstrip("/").rsplit("/", 1)
            size, done = int(t.get("size") or 0), int(t.get("bytesTransferred") or 0)
            out.append({
                "id": t.get("id"), "peer": str(t.get("username") or "?"),
                "file": parts[-1], "folder": parts[0] if len(parts) > 1 else "",
                "size": size, "bytes": done, "pct": round(100 * done / size) if size else 0,
                "state": str(t.get("state") or ""), "speed_bps": float(t.get("averageSpeed") or 0),
                "started_at": t.get("startedAt"), "ended_at": t.get("endedAt"),
            })
        out.sort(key=lambda u: (u["started_at"] or "", u["file"]), reverse=True)
        return out

    async def cancel_all(self) -> int:
        n = 0
        for t in await self.client.downloads():
            if not str(t.get("state", "")).startswith("Completed"):
                await self.client.cancel_download(t["username"], t["id"])
                n += 1
        return n

    async def rescan_shares(self) -> None:
        await self.client.rescan_shares()
