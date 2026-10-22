from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .models import Query, RequestKind

_URL_RE = re.compile(r"https?://[^\s]+")
_NOISE_RE = re.compile(
    r"\s*[\(\[\|]?\s*\b(official\s*(music\s*)?(video|audio|visualizer|lyric\s*video)?|hd|hq|4k|"
    r"lyrics?|visualizer|full\s*track|free\s*(download|dl|d/l)|out\s*now|premiere)\b\s*[\)\]]?\s*",
    re.IGNORECASE,
)  # the \b anchors matter: without them "Lyrical Assassin" loses its "Lyric"
# "TB Premiere: A - T", "[XYZ Premiere] A - T": the whole tag, or _NOISE_RE leaves its "TB :" behind.
# No dash before "premiere", so "A - T (World Premiere)" is left to _NOISE_RE.
_PREMIERE_PREFIX_RE = re.compile(
    r"^\s*(?:[^-–—:|\[\]()]{0,40}?\bpremiere\s*[:|–—-]|[\[(][^\])]{0,40}?\bpremiere\s*[\])])\s*", re.IGNORECASE)
# Upper case on purpose: "[NM022]", "(SGR053)" are catalogue numbers, "(2019 Remaster)", "(1998)" and
# (via the lookahead) an all-caps "(PART 12)" are not
_CATNO = r"[A-Z]{2,6}\s?-?\d{2,5}[A-Z]?"
_CATNO_TAG_RE = re.compile(r"\s*[\[(]\s*(?!(?:PART|PT|VOL|NO|EP)[\s\d-])(" + _CATNO + r")\s*[\])]")
# A bare leading "SGR053 - A - T" needs 3+ digits: "UB40 - Red Red Wine - Live" is a band, not a label
_CATNO_LEAD_RE = re.compile(r"[A-Z]{2,6}\s?-?\d{3,5}[A-Z]?")
_LABEL_TAG_RE = re.compile(r"\s*[\[(]([^()\[\]]*\b(?:records|recordings|music))\s*[\])]", re.IGNORECASE)
_FILE_EXT_RE = re.compile(r"\.(?:wav|mp3|flac|aiff?|m4a)(?=\s*(?:[\[(]|$))", re.IGNORECASE)
_UNRELEASED_RE = re.compile(r"\s*(?:/\s*unreleased\b|[\[(]\s*unreleased\s*[\])])", re.IGNORECASE)
# Not "rmx": lossless.file_title leaves "(Space Tribe Rmx)" in the title tokens for its own version rule
_VERSION_WORDS = re.compile(
    r"\b(remix|mix|edit|version|dub|rework|refix|bootleg|flip|mash-?up|remaster(ed)?|live|instrumental|"
    r"acapella|extended|radio|club|vip|original)\b", re.IGNORECASE)
_TRAIL_RE = re.compile(r"\s*[\(\[]([^\)\]]+)[\)\]]\s*$")
_DASH_VERSION_RE = re.compile(r"\s+[-–—]\s+([^-–—]+)$")
_SPLIT_TOKENS = re.compile(r"[(\[]|[)\]]|\s+[-–—]\s+")


def _clean_url(url: str) -> str:
    p = urlparse(url)
    q = parse_qs(p.query)
    keep = {k: v for k, v in q.items() if k in ("v", "list")}
    return urlunparse((p.scheme, (p.hostname or "").lower(), p.path, "", urlencode(keep, doseq=True), ""))


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
    # The exact host or a subdomain of it: a substring test let `youtube.com.evil.example` through to
    # yt-dlp, which would fetch it with its generic extractor.
    name = (p.hostname or "").lower()
    if not any(name == d or name.endswith("." + d) for d in ("youtube.com", "youtu.be")):
        return RequestKind.TEXT, None
    q = parse_qs(p.query)
    lid = q.get("list", [""])[0]
    if lid and not lid.startswith("RD"):  # RD… lists are YouTube's autoplay "mixes", not playlists
        # one canonical URL per playlist, whatever track it was shared from
        return RequestKind.YT_PLAYLIST, f"https://www.youtube.com/playlist?list={lid}"
    if name == "youtu.be" or name.endswith(".youtu.be"):
        vid = p.path.strip("/")
        return RequestKind.YT_TRACK, f"https://www.youtube.com/watch?v={vid}"
    # For regular tracks, keep only the video ID and the original scheme and host name (never a port or
    # a `user@`, which would have ridden along to yt-dlp from the netloc)
    vid = q.get("v", [""])[0]
    if vid:
        return RequestKind.YT_TRACK, f"{p.scheme}://{name}/watch?v={vid}"
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


def _unless_version(m: re.Match[str]) -> str:
    """Drop a bracketed tag, but never one that names a version or a featured artist ("(VIP 2019)")."""
    return m.group(0) if _VERSION_WORDS.search(m.group(1)) or _FEAT_RE.search(m.group(1)) else " "


def _strip_noise(s: str) -> str:
    s = _PREMIERE_PREFIX_RE.sub("", s)  # before the "|" cut, which would take "TB Premiere | A - T" whole
    s = re.sub(r"\s*\|.*$", "", s)  # "Title | Official Audio"
    s = _UNRELEASED_RE.sub(" ", s)
    prev = None
    while prev != s:
        prev = s
        s = _NOISE_RE.sub(" ", s)
    s = _CATNO_TAG_RE.sub(_unless_version, s)
    s = _LABEL_TAG_RE.sub(_unless_version, s)
    s = _FILE_EXT_RE.sub("", s)
    s = re.sub(r"(?<!\S)\*+(?!\S)", " ", s)  # what "*PREMIERE*" leaves; "*NSYNC" keeps its star
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


def _channel(uploader: str | None) -> str:
    return re.sub(r"\s*-\s*Topic$", "", uploader or "").strip()


def _strip_label_tag(s: str, uploader: str | None) -> str:
    """Drop a trailing [Label Name] / (Channel Name) that is neither a version nor a feature credit."""
    m = _TRAIL_RE.search(s)
    if not m:
        return s
    inner = m.group(1)
    channel = _channel(uploader).lower()
    if _VERSION_WORDS.search(inner) or _FEAT_RE.search(inner):
        return s
    if inner.strip().lower() == channel or m.group(0).lstrip().startswith("[") or "records" in inner.lower():
        return s[: m.start()].strip()
    return s


def parse_youtube_title(title: str, uploader: str | None = None, duration_s: int | None = None) -> Query:
    cleaned = _strip_label_tag(_strip_noise(title), uploader)
    artist, rest = _split_artist_title(cleaned)
    if artist and _CATNO_LEAD_RE.fullmatch(artist) and artist.lower() != _channel(uploader).lower() \
            and _split_artist_title(rest)[0] is not None:  # "SGR053 - A - T", never "A - T"
        artist, rest = _split_artist_title(rest)
    if artist is None and uploader:
        artist = _channel(uploader) or None
    t, version = parse_version(rest)
    return Query(raw=title, artist=artist, title=t, version=version, duration_s=duration_s)
