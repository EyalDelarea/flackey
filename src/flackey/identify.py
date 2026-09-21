from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .models import Query, RequestKind

_URL_RE = re.compile(r"https?://[^\s]+")
_NOISE_RE = re.compile(
    r"\s*[\(\[\|]?\s*\b(official\s*(music\s*)?(video|audio|visualizer|lyric\s*video)?|hd|hq|4k|"
    r"lyrics?|visualizer|full\s*track|free\s*download|out\s*now|premiere)\b\s*[\)\]]?\s*",
    re.IGNORECASE,
)  # the \b anchors matter: without them "Lyrical Assassin" loses its "Lyric"
_VERSION_WORDS = re.compile(
    r"\b(remix|mix|edit|version|dub|rework|bootleg|remaster(ed)?|live|instrumental|acapella|"
    r"extended|radio|club|vip|original)\b", re.IGNORECASE)
_TRAIL_RE = re.compile(r"\s*[\(\[]([^\)\]]+)[\)\]]\s*$")
_DASH_VERSION_RE = re.compile(r"\s+[-–—]\s+([^-–—]+)$")
_SPLIT_TOKENS = re.compile(r"[(\[]|[)\]]|\s+[-–—]\s+")


def _clean_url(url: str) -> str:
    p = urlparse(url)
    q = parse_qs(p.query)
    keep = {k: v for k, v in q.items() if k in ("v", "list")}
    return urlunparse((p.scheme, p.netloc, p.path, "", urlencode(keep, doseq=True), ""))


def _spotify_entity_id(path: str, entity_type: str) -> str | None:
    parts = [part for part in path.split("/") if part]
    if len(parts) >= 2 and parts[0] == entity_type:
        return parts[1]
    if len(parts) >= 3 and re.fullmatch(r"intl-[a-z]{2}", parts[0], re.IGNORECASE) and parts[1] == entity_type:
        return parts[2]
    return None


def classify(text: str) -> tuple[RequestKind, str | None]:
    m = _URL_RE.search(text)
    if not m:
        return RequestKind.TEXT, None
    url = m.group(0).rstrip(".,;)")
    p = urlparse(url)
    host = p.netloc.lower()
    if host == "open.spotify.com":
        track_id = _spotify_entity_id(p.path, "track")
        if track_id:
            return RequestKind.SPOTIFY_TRACK, f"https://open.spotify.com/track/{track_id}"
        playlist_id = _spotify_entity_id(p.path, "playlist")
        if playlist_id:
            return RequestKind.SPOTIFY_PLAYLIST, f"https://open.spotify.com/playlist/{playlist_id}"
        return RequestKind.TEXT, None
    if not any(h in host for h in ("youtube.com", "youtu.be")):
        return RequestKind.TEXT, None
    q = parse_qs(p.query)
    lid = q.get("list", [""])[0]
    if lid and not lid.startswith("RD"):  # RD… lists are YouTube's autoplay "mixes", not playlists
        # one canonical URL per playlist, whatever track it was shared from
        return RequestKind.YT_PLAYLIST, f"https://www.youtube.com/playlist?list={lid}"
    if "youtu.be" in host:
        vid = p.path.strip("/")
        return RequestKind.YT_TRACK, f"https://www.youtube.com/watch?v={vid}"
    # For regular tracks, keep only the video ID, preserve original scheme and host
    vid = q.get("v", [""])[0]
    if vid:
        return RequestKind.YT_TRACK, f"{p.scheme}://{p.netloc}/watch?v={vid}"
    return RequestKind.YT_TRACK, _clean_url(url)


def parse_version(title: str) -> tuple[str, str | None]:
    t = title.strip()
    m = _TRAIL_RE.search(t)
    if m and _VERSION_WORDS.search(m.group(1)):
        return t[: m.start()].strip(), m.group(1).strip()
    m = _DASH_VERSION_RE.search(t)
    if m and _VERSION_WORDS.search(m.group(1)):
        return t[: m.start()].strip(), m.group(1).strip()
    return t, None


def _strip_noise(s: str) -> str:
    s = re.sub(r"\s*\|.*$", "", s)  # "Title | Official Audio"
    prev = None
    while prev != s:
        prev = s
        s = _NOISE_RE.sub(" ", s)
    s = re.sub(r"\(\s*\)|\[\s*\]", "", s)
    return " ".join(s.split()).strip(" -–—")


def _split_artist_title(s: str) -> tuple[str | None, str]:
    """Artist and title around the first ` - ` that is not inside brackets. `Granada (Remix - 98)` is one
    title with a version in it, not artist `Granada (Remix` and title `98)` (issue #66)."""
    depth = 0
    for m in _SPLIT_TOKENS.finditer(s):
        tok = m.group(0)
        if tok in ("(", "["):
            depth += 1
        elif tok in (")", "]"):
            depth = max(depth - 1, 0)
        elif depth == 0:
            return s[: m.start()].strip(), s[m.end():].strip()
    return None, s.strip()


def parse_text(raw: str) -> Query:
    artist, rest = _split_artist_title(raw.strip())
    if artist is None:
        return Query(raw=raw.strip())
    title, version = parse_version(rest)
    return Query(raw=raw.strip(), artist=artist, title=title, version=version)


_FEAT_RE = re.compile(r"\b(feat|ft|featuring)\b\.?", re.IGNORECASE)


def _strip_label_tag(s: str, uploader: str | None) -> str:
    """Drop a trailing [Label Name] / (Channel Name) that is neither a version nor a feature credit."""
    m = _TRAIL_RE.search(s)
    if not m:
        return s
    inner = m.group(1)
    channel = re.sub(r"\s*-\s*Topic$", "", uploader or "").strip().lower()
    if _VERSION_WORDS.search(inner) or _FEAT_RE.search(inner):
        return s
    if inner.strip().lower() == channel or m.group(0).lstrip().startswith("[") or "records" in inner.lower():
        return s[: m.start()].strip()
    return s


def parse_youtube_title(title: str, uploader: str | None = None, duration_s: int | None = None) -> Query:
    cleaned = _strip_label_tag(_strip_noise(title), uploader)
    artist, rest = _split_artist_title(cleaned)
    if artist is None and uploader:
        artist = re.sub(r"\s*-\s*Topic$", "", uploader).strip() or None
    t, version = parse_version(rest)
    return Query(raw=title, artist=artist, title=t, version=version, duration_s=duration_s)
