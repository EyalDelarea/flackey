from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import UnauthorizedError
from telethon.tl.custom import Message

from ..deezer import DeezerApi, DeezerError
from ..models import Candidate, Query
from .base import SourceNotFound, SourceTimeout, SourceUnauthorized

_TRACK_RE = re.compile(r"^dz_track:(\d+):send$")
_LABEL_RE = re.compile(r"^\d+\.\s*(.*)$")
MAX_CANDIDATES = 7
MENU_CACHE = 200          # menu messages kept for fetch(); oldest evicted first
NO_RESULT_GRACE_S = 5.0   # a text reply with no keyboard, then silence this long, means "nothing found"
EXT_BY_MIME = {"audio/mpeg": "mp3", "audio/flac": "flac", "audio/x-flac": "flac", "audio/wav": "wav"}


@dataclass
class ButtonInfo:
    text: str
    data: str
    row: int = 0
    col: int = 0


@dataclass
class ResultMenu:
    candidates: list[Candidate] = field(default_factory=list)
    deezer_enabled: bool = False
    deezer_toggle: ButtonInfo | None = None


def parse_result_menu(buttons: list[list[ButtonInfo]]) -> ResultMenu:
    menu = ResultMenu()
    rank = 0
    for ri, row in enumerate(buttons):
        for ci, b in enumerate(row):
            m = _TRACK_RE.match(b.data or "")
            if m:
                rank += 1
                label = _LABEL_RE.sub(r"\1", b.text).strip()
                artist, sep, title = label.partition(" - ")
                if not sep:
                    artist, title = "", label
                menu.candidates.append(Candidate(source="deezer_bot", source_ref=b.data, artist=artist.strip(),
                                                 title=title.strip(), deezer_id=int(m.group(1)), rank=rank))
            elif b.text.startswith("Deezer"):
                menu.deezer_toggle = ButtonInfo(b.text, b.data, ri, ci)
                menu.deezer_enabled = "✅" in b.text
    return menu


def _buttons(msg: Message) -> list[list[ButtonInfo]]:
    rows = []
    for ri, row in enumerate(msg.buttons or []):
        rows.append([ButtonInfo(b.text or "", (b.data or b"").decode(errors="replace"), ri, ci)
                     for ci, b in enumerate(row)])
    return rows


class DeezerBotSource:
    name = "deezer_bot"

    def __init__(self, client: TelegramClient, bot_username: str, deezer: DeezerApi,
                 search_timeout: float = 30, fetch_timeout: float = 90,
                 get_client: Callable[[], TelegramClient] | None = None):
        self._client = client
        self.get_client = get_client
        self.bot_username = bot_username
        self.deezer = deezer
        self.search_timeout = search_timeout
        self.fetch_timeout = fetch_timeout
        self._menus: dict[str, Message] = {}
        # The one part of the pipeline that genuinely cannot run for two tracks at once. There is a single
        # DM with the bot; Telethon refuses a second exclusive `conversation()` on the same chat, and even
        # if it did not, the bot's replies carry no request id, so two searches in flight would read each
        # other's menus. Everything after this -- Soulseek, verify, convert, file -- is per-track and runs
        # concurrently; this is the queue's only serial stretch.
        self._bot = asyncio.Lock()

    @property
    def client(self) -> TelegramClient:
        # Sign-out rebuilds the shared Telethon client (telegram.TelegramLogin.log_out); this source must
        # see the new one on the next call rather than keep a reference to the one that was just killed.
        return self.get_client() if self.get_client is not None else self._client

    async def _enrich(self, c: Candidate) -> Candidate:
        try:
            t = await self.deezer.track(c.deezer_id)
        except (DeezerError, ValueError):  # ValueError: a non-JSON 200 (e.g. JSONDecodeError); enrichment is optional
            return c
        c.isrc, c.duration_s = t.isrc, t.duration_s
        if t.artist:
            c.artist = t.artist
        if t.title:
            c.title = t.title
        c.mix_name = t.title_version
        return c

    async def search(self, query: Query) -> list[Candidate]:
        async with self._bot:
            return await self._search_held(query)

    async def _search_held(self, query: Query) -> list[Candidate]:
        """The body of `search`, for callers that are already holding `_bot`. `asyncio.Lock` is not
        reentrant, so `fetch`'s menu-lost path has to reach the search this way or block on itself."""
        try:
            async with self.client.conversation(self.bot_username, timeout=self.search_timeout) as conv:
                await conv.send_message(query.search_text())
                msg: Message = await conv.get_response()
                while not msg.buttons:
                    # The bot's "nothing found" reply has not been captured yet (protocol doc §3). A keyboard-less
                    # text followed by silence is treated as not-found instead of waiting for the full timeout.
                    try:
                        msg = await asyncio.wait_for(conv.get_response(), timeout=NO_RESULT_GRACE_S)
                    except TimeoutError:
                        raise SourceNotFound(f"source bot replied without results: {(msg.text or '')[:80]}") from None
                menu = parse_result_menu(_buttons(msg))
                if not menu.deezer_enabled and menu.deezer_toggle is not None:
                    # Register the edit listener *before* clicking (the edit can arrive first), then click by
                    # position, never by data: "Tracks ✅" one row up carries the same "page:1".
                    edited = conv.wait_event(events.MessageEdited(chats=await conv.get_input_chat()))
                    await msg.click(menu.deezer_toggle.row, menu.deezer_toggle.col)
                    msg = (await edited).message
                    menu = parse_result_menu(_buttons(msg))
        except UnauthorizedError as e:
            raise SourceUnauthorized(f"Telegram session rejected ({e.__class__.__name__})") from e
        except TimeoutError as e:
            raise SourceTimeout("source bot did not answer the search") from e
        if not menu.candidates:
            raise SourceNotFound("no Deezer results")
        cands = menu.candidates[:MAX_CANDIDATES]
        for c in cands:
            self._menus[c.source_ref] = msg
        while len(self._menus) > MENU_CACHE:
            self._menus.pop(next(iter(self._menus)))
        return [await self._enrich(c) for c in cands]

    async def _ask_for_file(self, cand: Candidate) -> Message:
        """Click the candidate's button and wait for the bot to answer with the audio document. The caller
        holds `_bot`: this is the half of `fetch` that talks to the shared conversation."""
        menu_msg = self._menus.get(cand.source_ref)
        if menu_msg is None:
            # menu lost (restart): re-run the search to get a fresh menu. `_search_held`, not `search` --
            # we are already inside the lock and `asyncio.Lock` would deadlock rather than recurse.
            await self._search_held(Query(raw=f"{cand.artist} {cand.title}"))
            menu_msg = self._menus.get(cand.source_ref)
            if menu_msg is None:
                raise SourceNotFound("candidate no longer offered by the source bot")
        async with self.client.conversation(self.bot_username, timeout=self.fetch_timeout) as conv:
            await menu_msg.click(data=cand.source_ref.encode())  # track buttons have unique data
            while True:
                # This conversation sent nothing itself, so anchor on the menu message: a bare
                # get_response() raises "No message was sent previously". Repeated calls with the same
                # anchor advance through the bot's replies (progress text, then the audio document).
                msg: Message = await conv.get_response(menu_msg)
                if msg.document is not None:
                    return msg

    async def fetch(self, cand: Candidate, dest_dir: Path) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            async with self._bot:
                msg = await self._ask_for_file(cand)
            # The download is outside the lock as well as outside the conversation: the document is ours
            # already and pulling its bytes says nothing to the bot. Holding `_bot` across it would put
            # every track's file behind every other track's for no reason at all.
            ext = EXT_BY_MIME.get(msg.document.mime_type or "", None)
            if ext is None and msg.file and msg.file.name and "." in msg.file.name:
                ext = msg.file.name.rsplit(".", 1)[1].lower()
            dest = dest_dir / f"{cand.deezer_id}.{ext or 'bin'}"
            # Outside the conversation (the file transfer is not a conversation message), so it needs
            # its own timeout and must stay under the same except clauses: a session expiring mid-download
            # must pause the worker (SourceUnauthorized), not surface as a generic error.
            await asyncio.wait_for(self.client.download_media(msg, file=str(dest)), timeout=self.fetch_timeout)
        except UnauthorizedError as e:
            raise SourceUnauthorized(f"Telegram session rejected ({e.__class__.__name__})") from e
        except TimeoutError as e:
            raise SourceTimeout("source bot did not send the file") from e
        return dest
