from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar

from .models import (
    INFLIGHT_STATES,
    TERMINAL_STATES,
    Candidate,
    CatalogTrack,
    Evidence,
    LosslessAttempt,
    Playlist,
    Query,
    Rejection,
    Request,
    RequestKind,
    RequestState,
    Track,
)
from .models import norm as models_norm

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  raw_text TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
  playlist_id INTEGER, playlist_position INTEGER, source_url TEXT,
  query_artist TEXT, query_title TEXT, query_version TEXT, query_duration_s INTEGER,
  chosen_candidate_id INTEGER, catalog_track_id INTEGER, confidence INTEGER,
  flag_reason TEXT, error_message TEXT, attempts INTEGER NOT NULL DEFAULT 0, retry_after TEXT, track_id INTEGER,
  lossless_retry INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, rank INTEGER NOT NULL,
  source TEXT NOT NULL, source_ref TEXT NOT NULL, artist TEXT NOT NULL, title TEXT NOT NULL,
  mix_name TEXT, duration_s INTEGER, deezer_id INTEGER, isrc TEXT, score INTEGER, catalog_track_id INTEGER,
  -- Whether Deezer has a 30 s sample for this record, so the page knows before it renders whether to draw a
  -- play control. Three states: 1 = yes, 0 = no, NULL = never resolved -- every row written before this
  -- column existed, every candidate from a source with no Deezer record, and every one whose enrichment
  -- call failed. Deliberately nullable with no DEFAULT: a default would collapse "unknown" into "no", and
  -- the page treats the two differently.
  has_preview INTEGER
);
CREATE TABLE IF NOT EXISTS catalog_tracks (
  id INTEGER PRIMARY KEY, isrc TEXT, artist TEXT NOT NULL, title TEXT NOT NULL, mix_name TEXT NOT NULL,
  label TEXT NOT NULL, genre TEXT NOT NULL, sub_genre TEXT, catalog_number TEXT, release_name TEXT,
  release_date TEXT, bpm INTEGER, key TEXT, duration_ms INTEGER, artwork_url TEXT, fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tracks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, fmt TEXT NOT NULL,
  bitrate_kbps INTEGER NOT NULL, cutoff_hz INTEGER NOT NULL, file_size INTEGER NOT NULL,
  artist TEXT NOT NULL, title TEXT NOT NULL, mix_name TEXT NOT NULL, duration_s INTEGER,
  isrc TEXT, catalog_track_id INTEGER, request_id INTEGER, added_at TEXT NOT NULL,
  verified_at TEXT, spectrogram_path TEXT,
  artist_norm TEXT NOT NULL, title_norm TEXT NOT NULL, mix_norm TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tracks_isrc ON tracks(isrc);
CREATE TABLE IF NOT EXISTS playlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_url TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
  playlist_id INTEGER NOT NULL, track_id INTEGER NOT NULL, position INTEGER NOT NULL,
  PRIMARY KEY (playlist_id, track_id)
);
CREATE TABLE IF NOT EXISTS rejections (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, reason TEXT NOT NULL,
  bitrate_kbps INTEGER, cutoff_hz INTEGER, spectrogram_path TEXT, created_at TEXT NOT NULL,
  -- What the file failed, as a value rather than as prose the page has to parse: 'quality' (the spectral
  -- check said the audio is not what it claims) or 'different_recording' (the audio is genuine, it is the
  -- wrong track). The page draws a different explanation for each, and reading the kind is the only way it
  -- can tell them apart -- `reason` is written for a human and `cutoff_hz` is absent on both a
  -- different-recording rejection and an unsupported-format one.
  kind TEXT NOT NULL DEFAULT 'quality',
  -- Where the refused file was kept, for the one rejection the owner may overrule. A 'different_recording'
  -- verdict says the audio is genuine and not the track that was asked for, which is a judgement an ear can
  -- overturn -- an old track remastered or re-mixed can score below the floor and still be the copy the
  -- owner wants (issue #92). So that file is moved aside instead of deleted: this path is what the row
  -- plays and what "Keep it anyway" files. NULL on every other rejection, and on one whose file has since
  -- been filed or removed.
  audio_path TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS lossless_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, provider TEXT NOT NULL,
  created_at TEXT NOT NULL, query TEXT NOT NULL, outcome TEXT, report_json TEXT, timeline_json TEXT,
  fingerprint_json TEXT, spectrogram_path TEXT, first_byte_ms INTEGER, total_ms INTEGER, raw_dir TEXT
);
CREATE INDEX IF NOT EXISTS lossless_attempts_request ON lossless_attempts(request_id);
-- The ON DELETE CASCADE below does nothing: SQLite ignores foreign keys unless the connection sets
-- `PRAGMA foreign_keys = ON`, and `Store.__init__` deliberately does not (turning enforcement on would
-- change the behaviour of every other table at once). The clause is kept because it documents the
-- relationship and because CREATE TABLE IF NOT EXISTS cannot rewrite the databases already carrying it --
-- editing it out here would only make this file disagree with them. Enforcement is manual: `delete_request`
-- and `delete_requests` name this table explicitly, and anything added here must be added there too.
CREATE TABLE IF NOT EXISTS request_references (
  request_id INTEGER PRIMARY KEY REFERENCES requests(id) ON DELETE CASCADE,
  json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS track_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT, track_id INTEGER NOT NULL, kind TEXT NOT NULL,
  value_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS track_evidence_track ON track_evidence(track_id);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


norm = models_norm  # one normaliser for store, match and catalog (spec §5.1)


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.executescript(SCHEMA)
        # The schema script is CREATE IF NOT EXISTS only; columns added after a table shipped go here.
        self._ensure_column("tracks", "source", "TEXT NOT NULL DEFAULT 'deezer_bot'")
        self._ensure_column("tracks", "source_fmt", "TEXT")
        self._ensure_column("tracks", "bit_depth", "INTEGER")
        self._ensure_column("tracks", "sample_rate", "INTEGER")
        self._ensure_column("candidates", "has_preview", "INTEGER")
        self._ensure_column("rejections", "kind", "TEXT NOT NULL DEFAULT 'quality'")
        self._ensure_column("rejections", "audio_path", "TEXT")
        self._ensure_column("requests", "fetch_source", "TEXT")
        self._ensure_column("requests", "lossless_retry", "INTEGER NOT NULL DEFAULT 0")
        # Where a request was when it failed. Only `error` needs it: the other three failure states say
        # where they stopped by their own name. Rows written before the column keep NULL -- the stage is
        # genuinely not recorded for them and a guess reads worse than an honest gap.
        self._ensure_column("requests", "failed_stage", "TEXT")
        self._ensure_column("requests", "reviewed", "INTEGER NOT NULL DEFAULT 0")
        # The column was added with a DEFAULT of 0, which is a lie about any request that is parked right
        # now: being in awaiting_review *is* the record that it was sent for review. Without this the rung
        # would disappear from under the owner the moment they answered the question they came back to.
        # Safe to repeat on every open -- it only ever states what the row's own state already says.
        self.conn.execute("UPDATE requests SET reviewed=1 WHERE state='awaiting_review' AND reviewed=0")
        self.conn.commit()
        self._renormalize()
        self.listeners: list[Callable[[str, int], None]] = []

    def _emit(self, kind: str, oid: int) -> None:
        for fn in list(self.listeners):
            fn(kind, oid)

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            self.conn.commit()

    def _renormalize(self) -> None:
        """Recompute the *_norm columns so rows written by an older normaliser still match (cheap: a
        library holds thousands of rows at most)."""
        rows = self.conn.execute("SELECT id, artist, title, mix_name FROM tracks").fetchall()
        self.conn.executemany("UPDATE tracks SET artist_norm=?, title_norm=?, mix_norm=? WHERE id=?",
                              [(norm(r["artist"]), norm(r["title"]), norm(r["mix_name"]), r["id"]) for r in rows])
        self.conn.commit()

    # ---- requests -------------------------------------------------------
    def add_request(self, raw_text: str, kind: RequestKind, *, source_url: str | None = None,
                    playlist_id: int | None = None, playlist_position: int | None = None,
                    query: Query | None = None) -> int:
        q = query or Query(raw=raw_text)
        cur = self.conn.execute(
            "INSERT INTO requests (created_at, updated_at, raw_text, kind, state, playlist_id, "
            "playlist_position, source_url, query_artist, query_title, query_version, query_duration_s) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), _now(), raw_text, str(kind), str(RequestState.QUEUED), playlist_id,
             playlist_position, source_url, q.artist, q.title, q.version, q.duration_s),
        )
        self.conn.commit()
        rid = int(cur.lastrowid)
        self._emit("request", rid)
        return rid

    def _row_to_request(self, r: sqlite3.Row) -> Request:
        d = dict(r)
        d["kind"] = RequestKind(d["kind"])
        d["state"] = RequestState(d["state"])
        return Request(**d)

    def get_request(self, request_id: int) -> Request:
        r = self.conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if r is None:
            raise KeyError(request_id)
        return self._row_to_request(r)

    def list_requests(self, states: set[RequestState] | None = None, limit: int = 200) -> list[Request]:
        if states:
            marks = ",".join("?" * len(states))
            rows = self.conn.execute(
                f"SELECT * FROM requests WHERE state IN ({marks}) ORDER BY id DESC LIMIT ?",
                [str(s) for s in states] + [limit]).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_request(r) for r in rows]

    def list_recent_requests(self, limit: int = 500) -> list[Request]:
        rows = self.conn.execute("SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_request(r) for r in rows]

    def due_queued(self, limit: int | None = None) -> list[Request]:
        """Every queued request whose backoff (if any) has passed, oldest first. The worker starts all of
        them at once, so this is the whole batch rather than the head of a line; `limit` caps it when the
        owner has set `max_concurrent_requests`."""
        sql = ("SELECT * FROM requests WHERE state=? AND (retry_after IS NULL OR retry_after <= ?) "
               "ORDER BY id ASC")
        args: list = [str(RequestState.QUEUED), _now()]
        if limit is not None:
            sql, args = sql + " LIMIT ?", [*args, limit]
        return [self._row_to_request(r) for r in self.conn.execute(sql, args).fetchall()]

    def open_request_for_url(self, source_url: str) -> Request | None:
        marks = ",".join("?" * len(TERMINAL_STATES))
        r = self.conn.execute(
            f"SELECT * FROM requests WHERE source_url=? AND state NOT IN ({marks}) ORDER BY id LIMIT 1",
            [source_url] + [str(s) for s in TERMINAL_STATES]).fetchone()
        return None if r is None else self._row_to_request(r)

    def filed_request_for_url(self, source_url: str) -> Request | None:
        r = self.conn.execute(
            "SELECT * FROM requests WHERE source_url=? AND track_id IS NOT NULL ORDER BY id DESC LIMIT 1",
            (source_url,)).fetchone()
        return None if r is None else self._row_to_request(r)

    def latest_playlist_request_for_url(self, playlist_id: int, source_url: str) -> Request | None:
        r = self.conn.execute(
            "SELECT * FROM requests WHERE playlist_id=? AND source_url=? ORDER BY id DESC LIMIT 1",
            (playlist_id, source_url)).fetchone()
        return None if r is None else self._row_to_request(r)

    def update_request(self, request_id: int, **fields) -> None:
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [str(v) if isinstance(v, (RequestState, RequestKind)) else v for v in fields.values()]
        self.conn.execute(f"UPDATE requests SET {cols} WHERE id=?", vals + [request_id])
        self.conn.commit()
        self._emit("request", request_id)

    def set_state(self, request_id: int, state: RequestState, *, error_message: str | None = None,
                  flag_reason: str | None = None) -> None:
        fields: dict = {"state": state}
        if error_message is not None:
            fields["error_message"] = error_message
        if flag_reason is not None:
            fields["flag_reason"] = flag_reason
        self.update_request(request_id, **fields)

    def reset_inflight(self) -> int:
        marks = ",".join("?" * len(INFLIGHT_STATES))
        cur = self.conn.execute(
            f"UPDATE requests SET state=?, updated_at=? WHERE state IN ({marks})",
            [str(RequestState.QUEUED), _now()] + [str(s) for s in INFLIGHT_STATES])
        self.conn.commit()
        self._emit("queue", 0)
        return cur.rowcount

    def delete_request(self, request_id: int) -> None:
        """Forget one request's row, candidates, rejections, lossless attempts and acoustic reference. Never
        touches tracks/playlist_tracks: a filed track and its playlist membership outlive the request that
        produced it.

        Every child table is named here on purpose. `request_references` declares ON DELETE CASCADE, but this
        connection does not set `PRAGMA foreign_keys = ON`, so nothing in SQLite enforces it -- see the note
        above the table in SCHEMA. A child added to the schema and not added here leaks a row per delete,
        which is exactly what `request_references` did until issue #61."""
        if self.conn.execute("SELECT 1 FROM requests WHERE id=?", (request_id,)).fetchone() is None:
            raise KeyError(request_id)
        self.conn.execute("DELETE FROM candidates WHERE request_id=?", (request_id,))
        self.conn.execute("DELETE FROM rejections WHERE request_id=?", (request_id,))
        self.conn.execute("DELETE FROM lossless_attempts WHERE request_id=?", (request_id,))
        self.conn.execute("DELETE FROM request_references WHERE request_id=?", (request_id,))
        self.conn.execute("DELETE FROM requests WHERE id=?", (request_id,))
        self.conn.commit()
        self._emit("queue", 0)

    def delete_requests(self, states: set[RequestState]) -> list[int]:
        """Bulk-delete every request whose state is in `states`, plus their candidates, rejections, lossless
        attempts and acoustic references. Returns the deleted ids. Never touches tracks/playlist_tracks.

        Same hand-rolled cleanup as `delete_request`, and for the same reason: no foreign key in this
        database is enforced. Every child delete has to run before the parent rows go, or its `IN (sub)`
        stops matching anything."""
        if not states:
            return []
        marks = ",".join("?" * len(states))
        params = [str(s) for s in states]
        sub = f"SELECT id FROM requests WHERE state IN ({marks})"
        ids = [r[0] for r in self.conn.execute(sub, params)]
        if ids:
            self.conn.execute(f"DELETE FROM candidates WHERE request_id IN ({sub})", params)
            self.conn.execute(f"DELETE FROM rejections WHERE request_id IN ({sub})", params)
            self.conn.execute(f"DELETE FROM lossless_attempts WHERE request_id IN ({sub})", params)
            self.conn.execute(f"DELETE FROM request_references WHERE request_id IN ({sub})", params)
            self.conn.execute(f"DELETE FROM requests WHERE state IN ({marks})", params)
            self.conn.commit()
            self._emit("queue", 0)
        return sorted(ids)

    # ---- candidates -----------------------------------------------------
    def add_candidates(self, request_id: int, candidates: list[Candidate]) -> list[Candidate]:
        """Replace this request's candidate list with what the search just returned.

        Replace, not append: the list is the current search's answer, not a log of every pass. It used to be
        written at most once per request, because the branch that writes it was terminal; since issue #69 a
        request with no chosen record goes to Soulseek and comes back round the retry ladder, so an append
        left one set of rows per pass on the Choose list and on the request page.

        The one pointer at a candidate id that outlives a pass is `requests.chosen_candidate_id` (no foreign
        key enforces it, and nothing else in the schema references a candidate), so that row is kept
        whatever happens. In practice the caller only reaches here when it is NULL -- `_process` resumes
        from the chosen candidate instead of searching -- but stranding it would turn a retry into a
        KeyError deep in `upgrade()` or the sweep, which is too sharp an edge to leave to the caller.
        `IS NOT` is SQLite's null-safe comparison: with no choice recorded it keeps nothing."""
        self.conn.execute(
            "DELETE FROM candidates WHERE request_id=? "
            "AND id IS NOT (SELECT chosen_candidate_id FROM requests WHERE id=?)", (request_id, request_id))
        out = []
        for c in candidates:
            cur = self.conn.execute(
                "INSERT INTO candidates (request_id, rank, source, source_ref, artist, title, mix_name, "
                "duration_s, deezer_id, isrc, score, catalog_track_id, has_preview) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, c.rank, c.source, c.source_ref, c.artist, c.title, c.mix_name,
                 c.duration_s, c.deezer_id, c.isrc, c.score, c.catalog_track_id, c.has_preview))
            out.append(Candidate(**{**c.__dict__, "id": int(cur.lastrowid), "request_id": request_id}))
        self.conn.commit()
        return out

    def _row_to_candidate(self, r: sqlite3.Row) -> Candidate:
        d = dict(r)
        # SQLite hands back 1/0/NULL; the page branches on all three, and a bare 1 would reach the JSON as
        # `1` rather than `true`. NULL stays None -- "never resolved" is not "no sample".
        d["has_preview"] = None if d["has_preview"] is None else bool(d["has_preview"])
        return Candidate(**d)

    def get_candidates(self, request_id: int) -> list[Candidate]:
        rows = self.conn.execute("SELECT * FROM candidates WHERE request_id=? ORDER BY rank", (request_id,))
        return [self._row_to_candidate(r) for r in rows]

    def get_candidate(self, candidate_id: int) -> Candidate:
        r = self.conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if r is None:
            raise KeyError(candidate_id)
        return self._row_to_candidate(r)

    # ---- catalog --------------------------------------------------------
    def upsert_catalog_track(self, t: CatalogTrack) -> None:
        self.conn.execute(
            "INSERT INTO catalog_tracks (id, isrc, artist, title, mix_name, label, genre, sub_genre, "
            "catalog_number, release_name, release_date, bpm, key, duration_ms, artwork_url, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET isrc=excluded.isrc, "
            "artist=excluded.artist, title=excluded.title, mix_name=excluded.mix_name, label=excluded.label, "
            "genre=excluded.genre, sub_genre=excluded.sub_genre, catalog_number=excluded.catalog_number, "
            "release_name=excluded.release_name, release_date=excluded.release_date, bpm=excluded.bpm, "
            "key=excluded.key, duration_ms=excluded.duration_ms, artwork_url=excluded.artwork_url, "
            "fetched_at=excluded.fetched_at",
            (t.id, t.isrc, t.artist, t.title, t.mix_name, t.label, t.genre, t.sub_genre, t.catalog_number,
             t.release_name, t.release_date, t.bpm, t.key, t.duration_ms, t.artwork_url, _now()))
        self.conn.commit()

    def get_catalog_track(self, track_id: int) -> CatalogTrack | None:
        r = self.conn.execute("SELECT * FROM catalog_tracks WHERE id=?", (track_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d.pop("fetched_at")
        return CatalogTrack(**d)

    # ---- tracks ---------------------------------------------------------
    def add_track(self, *, path: Path, fmt: str, bitrate_kbps: int, cutoff_hz: int, file_size: int,
                  artist: str, title: str, mix_name: str, duration_s: int | None, isrc: str | None,
                  catalog_track_id: int | None, request_id: int | None,
                  spectrogram_path: Path | None = None, source: str = "deezer_bot",
                  source_fmt: str | None = None, bit_depth: int | None = None,
                  sample_rate: int | None = None) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO tracks (path, fmt, bitrate_kbps, cutoff_hz, file_size, artist, title, mix_name, "
            "duration_s, isrc, catalog_track_id, request_id, added_at, verified_at, spectrogram_path, "
            "source, source_fmt, bit_depth, sample_rate, "
            "artist_norm, title_norm, mix_norm) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(path), fmt, bitrate_kbps, cutoff_hz, file_size, artist, title, mix_name, duration_s, isrc,
             catalog_track_id, request_id, now, now, str(spectrogram_path) if spectrogram_path else None,
             source, source_fmt, bit_depth, sample_rate,
             norm(artist), norm(title), norm(mix_name)))
        self.conn.commit()
        tid = int(cur.lastrowid)
        self._emit("track", tid)
        return tid

    def _row_to_track(self, r: sqlite3.Row) -> Track:
        d = dict(r)
        for k in ("artist_norm", "title_norm", "mix_norm"):
            d.pop(k)
        d["path"] = Path(d["path"])
        d["spectrogram_path"] = Path(d["spectrogram_path"]) if d.get("spectrogram_path") else None
        return Track(**d)

    def update_track(self, track_id: int, **fields) -> None:
        """In place, keeping the id. An upgrade re-paths a filed track (the lossy .mp3 becomes a .wav), and
        `playlist_tracks` and `track_evidence` both key on the track id -- replacing the row instead would
        orphan them, and `tracks.path` is UNIQUE so the old row could not even stay behind."""
        if "path" in fields:
            fields["path"] = str(fields["path"])
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE tracks SET {cols} WHERE id=?", list(fields.values()) + [track_id])
        self.conn.commit()
        self._emit("track", track_id)

    def lossy_tracks(self, limit: int = 500) -> list[Track]:
        """Filed tracks still on the lossy copy: `source_fmt` is set only when a lossless provider supplied
        the file, so its absence is exactly "no lossless copy was obtained" -- and it names no provider,
        which keeps this right for the next one. Newest first, since those are the ones whose peers are
        most likely still online."""
        rows = self.conn.execute(
            "SELECT * FROM tracks WHERE source_fmt IS NULL ORDER BY added_at DESC, id DESC LIMIT ?",
            (limit,)).fetchall()
        return [self._row_to_track(r) for r in rows]

    def playlist_ids_for_track(self, track_id: int) -> list[int]:
        return [r[0] for r in self.conn.execute(
            "SELECT playlist_id FROM playlist_tracks WHERE track_id=? ORDER BY playlist_id", (track_id,))]

    def get_track(self, track_id: int) -> Track:
        r = self.conn.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
        if r is None:
            raise KeyError(track_id)
        return self._row_to_track(r)

    def list_tracks(self, search: str | None = None, limit: int = 500, playlist_id: int | None = None) -> list[Track]:
        where, params = [], []
        catalog_join = ""
        if search:
            like = f"%{norm(search)}%"
            where.append("(t.artist_norm LIKE ? OR t.title_norm LIKE ? OR t.mix_norm LIKE ? OR lower(c.label) LIKE ?)")
            params += [like, like, like, like]
            catalog_join = "LEFT JOIN catalog_tracks c ON c.id=t.catalog_track_id"
        if playlist_id is not None:
            join = f"{catalog_join} JOIN playlist_tracks p ON p.track_id=t.id AND p.playlist_id=?"
            params.insert(0, playlist_id)
            order = "p.position, t.id"
        else:
            join, order = catalog_join, "t.id DESC"
        sql = f"SELECT t.* FROM tracks t {join} {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY {order} LIMIT ?"
        rows = self.conn.execute(sql, params + [limit]).fetchall()
        return [self._row_to_track(r) for r in rows]

    def find_track_by_isrc(self, isrc: str) -> Track | None:
        r = self.conn.execute("SELECT * FROM tracks WHERE isrc=? LIMIT 1", (isrc,)).fetchone()
        return None if r is None else self._row_to_track(r)

    def find_track_by_meta(self, artist: str, title: str, mix_name: str, duration_s: int | None,
                           tolerance_s: int = 3) -> Track | None:
        rows = self.conn.execute(
            "SELECT * FROM tracks WHERE artist_norm=? AND title_norm=? AND mix_norm=?",
            (norm(artist), norm(title), norm(mix_name))).fetchall()
        for r in rows:
            if duration_s is None or r["duration_s"] is None or abs(r["duration_s"] - duration_s) <= tolerance_s:
                return self._row_to_track(r)
        return None

    def delete_track(self, track_id: int) -> None:
        """Forget a track whose file is gone: its row and its playlist memberships."""
        self.conn.execute("DELETE FROM playlist_tracks WHERE track_id=?", (track_id,))
        self.conn.execute("DELETE FROM tracks WHERE id=?", (track_id,))
        self.conn.commit()
        self._emit("queue", 0)

    def find_track_by_path(self, path: Path) -> Track | None:
        r = self.conn.execute("SELECT * FROM tracks WHERE path=?", (str(path),)).fetchone()
        return None if r is None else self._row_to_track(r)

    # ---- playlists ------------------------------------------------------
    def upsert_playlist(self, source_url: str, name: str) -> int:
        self.conn.execute(
            "INSERT INTO playlists (source_url, name, created_at, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(source_url) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at",
            (source_url, name, _now(), _now()))
        self.conn.commit()
        pid = int(self.conn.execute("SELECT id FROM playlists WHERE source_url=?", (source_url,)).fetchone()[0])
        self._emit("queue", 0)
        return pid

    def get_playlist(self, playlist_id: int) -> Playlist:
        r = self.conn.execute("SELECT * FROM playlists WHERE id=?", (playlist_id,)).fetchone()
        if r is None:
            raise KeyError(playlist_id)
        tracks = self.conn.execute(
            "SELECT track_id, position FROM playlist_tracks WHERE playlist_id=? ORDER BY position", (playlist_id,)
        ).fetchall()
        return Playlist(**dict(r), track_ids=[row[0] for row in tracks],
                        track_positions=[row[1] for row in tracks])

    def list_playlists(self) -> list[Playlist]:
        ids = [r[0] for r in self.conn.execute("SELECT id FROM playlists ORDER BY id")]
        return [self.get_playlist(i) for i in ids]

    def add_playlist_track(self, playlist_id: int, track_id: int, position: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO playlist_tracks (playlist_id, track_id, position) VALUES (?,?,?)",
            (playlist_id, track_id, position))
        self.conn.execute("UPDATE playlists SET updated_at=? WHERE id=?", (_now(), playlist_id))
        self.conn.commit()
        self._emit("queue", 0)

    # ---- rejections -----------------------------------------------------
    def add_rejection(self, request_id: int, reason: str, bitrate_kbps: int | None, cutoff_hz: int | None,
                      spectrogram_path: Path | None, *, kind: str = "quality",
                      audio_path: Path | None = None) -> int:
        """`kind` is what the file failed: 'quality' (the spectral check) or 'different_recording' (it is
        genuine audio of the wrong track). `audio_path` is the refused file, kept only for the second kind.
        Both keyword-only so no caller can drift into the positional slot that the spectrogram already
        holds."""
        cur = self.conn.execute(
            "INSERT INTO rejections (request_id, reason, bitrate_kbps, cutoff_hz, spectrogram_path, created_at, "
            "kind, audio_path) VALUES (?,?,?,?,?,?,?,?)",
            (request_id, reason, bitrate_kbps, cutoff_hz,
             None if spectrogram_path is None else str(spectrogram_path), _now(), kind,
             None if audio_path is None else str(audio_path)))
        self.conn.commit()
        return int(cur.lastrowid)

    def clear_rejection_audio(self, rejection_id: int) -> None:
        """The kept file is gone -- filed by "Keep it anyway", or deleted. The row stays as the record of
        what happened; only the promise that there is something to play is withdrawn."""
        self.conn.execute("UPDATE rejections SET audio_path=NULL WHERE id=?", (rejection_id,))
        self.conn.commit()

    def get_rejection(self, rejection_id: int) -> Rejection:
        r = self.conn.execute("SELECT * FROM rejections WHERE id=?", (rejection_id,)).fetchone()
        if r is None:
            raise KeyError(rejection_id)
        return Rejection(**dict(r))

    def get_rejection_for_request(self, request_id: int) -> Rejection | None:
        r = self.conn.execute("SELECT * FROM rejections WHERE request_id=? ORDER BY id DESC LIMIT 1",
                              (request_id,)).fetchone()
        return None if r is None else Rejection(**dict(r))

    def list_rejections(self, limit: int = 200) -> list[Rejection]:
        rows = self.conn.execute("SELECT * FROM rejections ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [Rejection(**dict(r)) for r in rows]

    # ---- lossless attempts and evidence (spec §11, §17, §18) --------------
    _ATTEMPT_JSON: ClassVar[dict[str, str]] = {
        "report": "report_json", "timeline": "timeline_json", "fingerprint": "fingerprint_json"}

    def add_attempt(self, request_id: int, provider: str, query: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO lossless_attempts (request_id, provider, created_at, query, timeline_json) VALUES (?,?,?,?,'[]')",
            (request_id, provider, _now(), query))
        self.conn.commit()
        return int(cur.lastrowid)

    def update_attempt(self, attempt_id: int, **fields) -> None:
        cols, vals = [], []
        for k, v in fields.items():
            col = self._ATTEMPT_JSON.get(k, k)
            cols.append(f"{col}=?")
            vals.append(json.dumps(v) if k in self._ATTEMPT_JSON else v)
        self.conn.execute(f"UPDATE lossless_attempts SET {', '.join(cols)} WHERE id=?", vals + [attempt_id])
        self.conn.commit()

    def _row_to_attempt(self, r: sqlite3.Row) -> LosslessAttempt:
        d = dict(r)
        return LosslessAttempt(
            id=d["id"], request_id=d["request_id"], provider=d["provider"], created_at=d["created_at"],
            query=d["query"], outcome=d["outcome"],
            report=json.loads(d["report_json"]) if d["report_json"] else None,
            timeline=json.loads(d["timeline_json"]) if d["timeline_json"] else [],
            fingerprint=json.loads(d["fingerprint_json"]) if d["fingerprint_json"] else None,
            spectrogram_path=d["spectrogram_path"], first_byte_ms=d["first_byte_ms"], total_ms=d["total_ms"],
            raw_dir=d["raw_dir"])

    def get_attempt(self, attempt_id: int) -> LosslessAttempt:
        r = self.conn.execute("SELECT * FROM lossless_attempts WHERE id=?", (attempt_id,)).fetchone()
        if r is None:
            raise KeyError(attempt_id)
        return self._row_to_attempt(r)

    def get_attempt_for_request(self, request_id: int) -> LosslessAttempt | None:
        r = self.conn.execute("SELECT * FROM lossless_attempts WHERE request_id=? ORDER BY id DESC LIMIT 1",
                              (request_id,)).fetchone()
        return None if r is None else self._row_to_attempt(r)

    def list_attempts(self, limit: int = 50, outcome: str | None = None) -> list[LosslessAttempt]:
        if outcome:
            rows = self.conn.execute("SELECT * FROM lossless_attempts WHERE outcome=? ORDER BY id DESC LIMIT ?",
                                     (outcome, limit)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM lossless_attempts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_attempt(r) for r in rows]

    def attempt_counts(self, since_hours: int = 24) -> dict[str, int]:
        since = (datetime.now(UTC) - timedelta(hours=since_hours)).isoformat(timespec="seconds")
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT COALESCE(outcome, 'open'), COUNT(*) FROM lossless_attempts WHERE created_at >= ? GROUP BY 1",
            (since,))}

    def mark_open_attempts_interrupted(self) -> int:
        cur = self.conn.execute("UPDATE lossless_attempts SET outcome='interrupted' WHERE outcome IS NULL")
        self.conn.commit()
        return cur.rowcount

    def get_reference(self, request_id: int) -> dict | None:
        """The request's acoustic reference (`fingerprint.AcousticReference.to_dict`). Its own table: a
        full fingerprint is ~50 KB and `list_requests` feeds the UI."""
        r = self.conn.execute("SELECT json FROM request_references WHERE request_id=?", (request_id,)).fetchone()
        return json.loads(r["json"]) if r else None

    def set_reference(self, request_id: int, reference: dict | None) -> None:
        with self.conn:
            if reference is None:
                self.conn.execute("DELETE FROM request_references WHERE request_id=?", (request_id,))
            else:
                self.conn.execute("INSERT INTO request_references(request_id, json) VALUES (?, ?) "
                                  "ON CONFLICT(request_id) DO UPDATE SET json=excluded.json",
                                  (request_id, json.dumps(reference)))

    def add_evidence(self, track_id: int, kind: str, value: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO track_evidence (track_id, kind, value_json, created_at) VALUES (?,?,?,?)",
            (track_id, kind, json.dumps(value), _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def clear_evidence(self, track_id: int) -> None:
        """After an upgrade the fingerprint and spectrogram rows describe a file that is gone; the new
        attempt writes its own."""
        self.conn.execute("DELETE FROM track_evidence WHERE track_id=?", (track_id,))
        self.conn.commit()

    def list_evidence(self, track_id: int) -> list[Evidence]:
        rows = self.conn.execute("SELECT * FROM track_evidence WHERE track_id=? ORDER BY id", (track_id,)).fetchall()
        return [Evidence(id=r["id"], track_id=r["track_id"], kind=r["kind"], value=json.loads(r["value_json"]),
                         created_at=r["created_at"]) for r in rows]

    # ---- stats / settings ----------------------------------------------
    def stats(self) -> dict:
        by_state = {r[0]: r[1] for r in self.conn.execute(
            "SELECT state, COUNT(*) FROM requests GROUP BY state")}
        tracks = self.conn.execute("SELECT COUNT(*), COALESCE(SUM(file_size),0) FROM tracks").fetchone()
        rejections = self.conn.execute("SELECT COUNT(*) FROM rejections").fetchone()[0]
        by_genre = {r[0]: r[1] for r in self.conn.execute(
            "SELECT c.genre, COUNT(*) FROM tracks t JOIN catalog_tracks c ON c.id=t.catalog_track_id "
            "GROUP BY c.genre ORDER BY 2 DESC")}
        by_label = {r[0]: r[1] for r in self.conn.execute(
            "SELECT c.label, COUNT(*) FROM tracks t JOIN catalog_tracks c ON c.id=t.catalog_track_id "
            "GROUP BY c.label ORDER BY 2 DESC")}
        per_week = [(r[0], r[1]) for r in self.conn.execute(
            "SELECT strftime('%Y-W%W', created_at), COUNT(*) FROM requests GROUP BY 1 ORDER BY 1")]
        by_source = {r[0]: r[1] for r in self.conn.execute("SELECT source, COUNT(*) FROM tracks GROUP BY source")}
        return {"requests_by_state": by_state, "tracks": tracks[0], "bytes": tracks[1],
                "rejections": rejections, "by_genre": by_genre, "by_label": by_label,
                "requests_per_week": per_week, "by_source": by_source}

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return default if r is None else r[0]

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET "
                          "value=excluded.value", (key, value))
        self.conn.commit()
