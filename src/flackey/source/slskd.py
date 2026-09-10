"""slskd REST adapter (spec §7): a thin HTTP client and the first LosslessProvider. Facts about the API that
the spike established are in the tests' docstring and in the spec; nothing here is guessed."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

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


def _remove_stale_file(dest: Path) -> None:
    """Clear whatever sits at the derived path before enqueueing, so slskd cannot dedup a fresh download onto a
    different (.NET-ticks-suffixed) name because the derived name was already taken. Safe only because lossless
    downloads are serialized; if two attempts ever ran concurrently, one job's pre-clean could delete the other's
    in-flight file at the same derived path. `dest` is built from peer-chosen strings - treat every failure mode
    as expected input, not a bug."""
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


class SoulseekProvider:
    name = "soulseek"

    def __init__(self, client: SlskdClient, downloads: Path, *, sleep=asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.client, self.downloads, self.sleep, self.clock = client, downloads, sleep, clock

    async def health(self) -> dict:
        try:
            app = await self.client.application()
        except LosslessUnavailable:
            return {"status": "unreachable", "username": None}
        server = app.get("server") or {}
        return {"status": "ok" if server.get("isLoggedIn") else "not_logged_in",
                "username": (app.get("user") or {}).get("username")}

    async def search(self, text: str, *, wait_s: float, on_raw: RawSink | None = None) -> list[LosslessFile]:
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
        _remove_stale_file(dest)                        # so a leftover file can't be mistaken for the fresh one
        await self.client.enqueue(file.username, file.path, file.size)
        queue_wait_s = first_byte_s if queue_wait_s is None else queue_wait_s
        stall_s = first_byte_s if stall_s is None else stall_s
        t0, active_at, first_byte_at, last_state, n = self.clock(), None, None, None, 0
        last_done, moved_at = 0, None
        while True:
            await self.sleep(poll_s)
            tr = await self._find(file)
            now = self.clock()
            if tr is None:
                # Never even acknowledged: no queue to be in, so this is the peer, not their popularity.
                if now - t0 > first_byte_s:
                    raise LosslessError("transfer never appeared in slskd", "first_byte_timeout")
                continue
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
                    return dest
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
