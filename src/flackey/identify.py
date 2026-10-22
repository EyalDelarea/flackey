from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .models import Query, RequestKind, norm

_URL_RE = re.compile(r"https?://[^\s]+")
# Right after &, x, ft or vs, "Premiere" is a credited artist ("A & Premiere - T"), not a premiere
_NOT_A_CREDIT = r"(?<!&\s)(?<!\bx\s)(?<!\bvs\s)(?<!\bvs\.\s)(?<!\bft\s)(?<!\bft\.\s)(?<!\bfeat\s)(?<!\bfeat\.\s)"
_NOISE_RE = re.compile(
    r"\s*[\(\[\|]?\s*\b(official\s*(music\s*)?(video|audio|visualizer|lyric\s*video)?|hd|hq|4k|"
    r"lyrics?|visualizer|full\s*track|free\s*(download|dl|d/l)|out\s*now|" + _NOT_A_CREDIT + r"premiere)\b\s*[\)\]]?\s*",
    re.IGNORECASE,
)  # the \b anchors matter: without them "Lyrical Assassin" loses its "Lyric"
# "TB Premiere: A - T", "[XYZ Premiere] A - T": the whole tag, or _NOISE_RE leaves its "TB :" behind.
# No dash before "premiere", so "A - T (World Premiere)" is left to _NOISE_RE.
_PREMIERE_PREFIX_RE = re.compile(
    r"^\s*(?:[^-–—:|\[\]()]{0,40}?" + _NOT_A_CREDIT + r"\bpremiere\s*[:|–—-]|[\[(][^\])]{0,40}?\bpremiere\s*[\])])\s*",
    re.IGNORECASE)
# Upper case on purpose: "[NM022]", "(SGR053)" are catalogue numbers, "(2019 Remaster)" and "(1998)" are
# not. Glued, 3+ letters and 3+ digits ("(UB40)", "(AK47)" stay), 4+ letters and 2 ("[HOMM12]"), or two
# letters and a zero-padded number ("[NM022]", where "(TB303)" is a synth); spaced or dashed, only a
# zero-padded number ("[CAT-001]"), so "(TR-808)", "(AREA 51)", "(BWV 1007)" stay.
_CATNO = r"(?:[A-Z]{3,6}\d{3,5}|[A-Z]{4,6}\d{2}|[A-Z]{2}0\d{2,4}|[A-Z]{2,6}[\s-]0\d{2,4})[A-Z]?"
_NOT_A_CATNO = r"(?!(?:VOLUME|VOL|NUMBER|NO|CD|DISC|PARTE|PART|PT|EP|LP)[\s\d-])"
_CATNO_TAG_RE = re.compile(r"\s*[\[(]\s*" + _NOT_A_CATNO + "(" + _CATNO + r")\s*[\])]")
_CATNO_LEAD_RE = re.compile(r"[A-Z]{2,6}\s?-?\d{3,5}[A-Z]?")
# "Music" is a real word in plenty of subtitles ("(Feel The Music)"), so it marks a label only in square
# brackets; parentheses need it to be the channel. "(Field Recordings)" is not a label either.
_LABEL_TAG_RE = re.compile(
    r"\s*([\[(])([^()\[\]]*\b(?:records|(?<!field\s)recordings|(music)))\s*[\])]", re.IGNORECASE)
_FILE_EXT_RE = re.compile(r"\.(?:wav|mp3|flac|aiff?|m4a)(?=\s*(?:[\[(]|$)|\s+[-–—]\s)", re.IGNORECASE)
# A spaced "/ unreleased" tag at the end, not "Up/Unreleased Love"
_UNRELEASED_RE = re.compile(
    r"\s*(?:\s/\s*unreleased(?=\s*(?:[\[(]|$))|[\[(]\s*unreleased\s*[\])])", re.IGNORECASE)
# Not "rmx": lossless.file_title leaves "(Space Tribe Rmx)" in the title tokens for its own version rule
_VERSION_WORDS = re.compile(
    r"\b(remix(ed)?|mix|edit|version|dub|rework|bootleg|remaster(ed)?|live|instrumental|"
    r"acapella|extended|radio|club|vip|original)\b", re.IGNORECASE)
# Flip, refix and mash-up are versions only as the last word, after whoever made them ("Louis Futon Flip",
# "Someone Refix 2023"): "(Flip Side)", "(Mash Up The Dance)", "(The Flip)", "(Lil Flip)" are titles.
_TAIL_VERSION_RE = re.compile(
    r"(?:^\s*(?:refix|mash[- ]?up)|\S\s+(?<!\bthe\s)(?<!\ba\s)(?<!\blil\s)(?<!\bdj\s)(?:flip|refix|mash[- ]?up))"
    r"(?:\s+(?:\d{2,4}|vip))?\s*$", re.IGNORECASE)
_FEAT_RE = re.compile(r"\b(feat|ft|featuring)\b\.?", re.IGNORECASE)
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


def _is_version(text: str) -> bool:
    return bool(_VERSION_WORDS.search(text) or _TAIL_VERSION_RE.search(text))


def parse_version(title: str) -> tuple[str, str | None]:
    t = title.strip()
    m = _TRAIL_RE.search(t)
    # "(feat. Lil Flip)", "(feat. Club Dogo)" credit someone; they are not a flip or a club mix
    if m and _is_version(m.group(1)) and not _FEAT_RE.match(m.group(1).strip()):
        return t[: m.start()].strip(), m.group(1).strip()
    m = _DASH_VERSION_RE.search(t)
    if m and _is_version(m.group(1)):
        return t[: m.start()].strip(), m.group(1).strip()
    return t, None


def _keeps_version(inner: str) -> bool:
    return _is_version(inner) or bool(_FEAT_RE.search(inner))


def _drop_catno(m: re.Match[str]) -> str:
    """A catalogue number, unless it is a version glued to digits: "(VIP2019)", "(RMX 2023)", "(ADE2019)"."""
    inner = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", m.group(1))
    keep = _keeps_version(inner) or re.search(r"(?i)\brmx\b", inner) or re.search(r"\b(19|20)\d\d\b", inner)
    return m.group(0) if keep else " "


def _strip_noise(s: str, channel: str = "") -> str:
    def drop_label(m: re.Match[str]) -> str:
        inner = m.group(2)
        if _keeps_version(inner) or (m.group(3) and m.group(1) == "(" and inner.strip().lower() != channel):
            return m.group(0)
        return " "

    s = _PREMIERE_PREFIX_RE.sub("", s)  # before the "|" cut, which would take "TB Premiere | A - T" whole
    # "Title | Official Audio", but "FREE DOWNLOAD | A - T" keeps its second part
    parts = s.split("|")
    s = next((p for p in parts if re.search(r"\w", _NOISE_RE.sub(" ", p))), parts[0])
    s = _UNRELEASED_RE.sub(" ", s)
    prev = None
    while prev != s:
        prev = s
        s = _NOISE_RE.sub(" ", s)
    s = _CATNO_TAG_RE.sub(_drop_catno, s)
    s = _LABEL_TAG_RE.sub(drop_label, s)
    s = _FILE_EXT_RE.sub("", s)
    s = re.sub(r"(?<!\S)\*+(?!\S)", " ", s)  # what "*PREMIERE*" leaves; "*NSYNC" keeps its star
    s = re.sub(r"(?:\s+[-–—])+(?=\s)", " -", s)  # "A - T - * - Extended Mix" left "T - - Extended Mix"
    s = re.sub(r"\(\s*\)|\[\s*\]|【\s*】", "", s)
    return " ".join(s.split()).strip(" -–—").lstrip(":| -–—")  # and the ":" of "FREE DL: A - T"


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


def _channel(uploader: str | None) -> str:
    return re.sub(r"\s*-\s*Topic$", "", uploader or "").strip()


def _strip_label_tag(s: str, uploader: str | None) -> str:
    """Drop a trailing [Label Name] / (Channel Name) that is neither a version nor a feature credit."""
    m = _TRAIL_RE.search(s)
    if not m:
        return s
    inner = m.group(1)
    channel = _channel(uploader).lower()
    if _keeps_version(inner):
        return s
    if inner.strip().lower() == channel or m.group(0).lstrip().startswith("[") or "records" in inner.lower():
        return s[: m.start()].strip()
    return s


def _clean(title: str, uploader: str | None) -> tuple[str, str]:
    """The title as `parse_youtube_title` reads it -- whitespace collapsed and capped -- and that with the
    upload junk taken out."""
    # One space per run and a cap far above YouTube's 100 characters: some of the patterns above are
    # quadratic or worse on long runs of spaces
    text = " ".join(title.split())[:300]
    return text, _strip_label_tag(_strip_noise(text, _channel(uploader).lower()), uploader)


def _read(cleaned: str, text: str, title: str, uploader: str | None, duration_s: int | None) -> Query:
    """One cleaned title read the way `parse_youtube_title` reads it: the first dash splits, the channel
    stands in for a missing artist, and `text` (the title before cleanup) is what is searched when cleanup
    leaves nothing."""
    channel = _channel(uploader)
    artist, rest = _split_artist_title(cleaned)
    raw_artist, raw_rest = _split_artist_title(text)
    if artist is None and raw_artist and re.search(r"\w", cleaned) \
            and not re.search(r"\w", _strip_noise(raw_rest, channel.lower())):
        # cleanup took the whole title side ("A - Free Dl", "A - .mp3"): keep the artist, search the title as is
        artist, rest = cleaned, raw_rest
    # "SGR053 - A - T", never "A - T" nor "UFO361 - T - Remix": the rest needs an artist of its own
    if artist and _CATNO_LEAD_RE.fullmatch(artist) and artist.lower() != channel.lower() \
            and _split_artist_title(parse_version(rest)[0])[0] is not None:
        artist, rest = _split_artist_title(rest)
    if artist is None and uploader:
        artist = channel or None
    t, version = parse_version(rest)
    if not re.search(r"\w", t):  # cleanup ate it all ("[FREE DL]", "Premiere:"): search for what was there
        t, version = (rest if re.search(r"\w", rest) or rest == raw_rest else text), None
    return Query(raw=title, artist=artist, title=t, version=version, duration_s=duration_s)


def parse_youtube_title(title: str, uploader: str | None = None, duration_s: int | None = None) -> Query:
    text, cleaned = _clean(title, uploader)
    return _read(cleaned, text, title, uploader, duration_s)


MAX_READINGS = 4
_BRACKET_RE = re.compile(r"\s*[\[(]([^()\[\]]*)[\])]")
# "A x B", "A & B", "A vs B": the first name alone is what a catalogue files a collaboration under more often
# than not. Spaces on both sides, so "Malcolm X" and "AC/DC" stay whole.
_CREDIT_SPLIT_RE = re.compile(r"\s+(?:x|&|vs\.?)\s+", re.IGNORECASE)


def _segments(s: str) -> list[str]:
    """The cleaned title cut at every dash that is not inside brackets (see `_split_artist_title`)."""
    out, depth, start = [], 0, 0
    for m in _SPLIT_TOKENS.finditer(s):
        tok = m.group(0)
        if tok in ("(", "["):
            depth += 1
        elif tok in (")", "]"):
            depth = max(depth - 1, 0)
        elif depth == 0:
            out.append(s[start:m.start()].strip())
            start = m.end()
    out.append(s[start:].strip())
    return out


def _alternative_split(cleaned: str, title: str, uploader: str | None, duration_s: int | None) -> Query | None:
    """The other place the artist could be. With two dashes ("Label - Artist - Title") the first one is as
    likely a label, a blog or a mix series as an artist, so the last dash splits and what sits between is
    the artist. With none, the channel the baseline borrowed is as likely a label's as the artist's (a
    "- Topic" channel is YouTube's own and always the artist), so the title alone is the other reading."""
    segs = _segments(cleaned)
    version = None
    # "A - T - Extended Mix" is two segments and a version; "A - B - T (Extended Mix)" is three
    if len(segs) >= 3 and _VERSION_WORDS.search(segs[-1]) and not re.search(r"[\[(]", segs[-1]):
        version = segs.pop()
    if len(segs) >= 3:
        t, v = parse_version(segs[-1])
        artist = " - ".join(segs[1:-1])
        return Query(raw=title, artist=artist, title=t, version=version or v, duration_s=duration_s)
    if len(segs) == 1 and uploader and not re.search(r"-\s*Topic$", uploader, re.IGNORECASE):
        t, v = parse_version(cleaned)
        return Query(raw=title, artist=None, title=t, version=v, duration_s=duration_s)
    return None


def _reading_key(q: Query) -> tuple[frozenset[str], ...]:
    version = None if q.version and re.search(r"\boriginal\b", q.version, re.IGNORECASE) else q.version
    return tuple(frozenset(norm(part).split()) for part in (q.artist, q.title, version))


def interpretations(title: str, uploader: str | None = None, duration_s: int | None = None) -> list[Query]:
    """The plausible readings of a noisy title, most likely first, and never more than MAX_READINGS. The
    first is always `parse_youtube_title`'s, the query the pipeline searches with; the others are what the
    same words could also mean -- a leftover bracket that is a tag rather than part of the title, the artist
    after a label, a collaboration under its first name. A title with only one sensible reading answers
    with just that one, which is the case for most."""
    text, cleaned = _clean(title, uploader)
    baseline = _read(cleaned, text, title, uploader, duration_s)
    readings = [baseline]
    debracketed = " ".join(_BRACKET_RE.sub(lambda m: m.group(0) if _keeps_version(m.group(1)) else " ",
                                           cleaned).split())
    if debracketed and debracketed != cleaned:
        readings.append(_read(debracketed, text, title, uploader, duration_s))
    alt = _alternative_split(cleaned, title, uploader, duration_s)
    if alt is not None:
        readings.append(alt)
    for r in list(readings):
        if r.artist and _CREDIT_SPLIT_RE.search(r.artist):
            first = _CREDIT_SPLIT_RE.split(r.artist, maxsplit=1)[0].strip()
            if first:
                readings.append(Query(raw=title, artist=first, title=r.title, version=r.version,
                                      duration_s=duration_s))
    seen: set[tuple[frozenset[str], ...]] = set()
    out: list[Query] = []
    for r in readings:
        key = _reading_key(r)
        if key in seen or not re.search(r"\w", r.title or ""):
            continue
        seen.add(key)
        out.append(r)
    return out[:MAX_READINGS] if len(out) >= 2 else [baseline]
