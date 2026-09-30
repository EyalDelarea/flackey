"""Report a bug from inside the app (issue #33).

Gathers the log, version, system release and displays, redacts them into a zip, and opens an email to
the developer with the report already written, next to the zip in Finder (File Explorer on Windows) for
the reporter to drag in.
No token, no server of ours, no account: the reporter's own email is the transport, and they press Send.

The repository is public, so `Redactor` is the security boundary. The preview the page shows and the
zip that gets written are built by the same `build_report`, so what the reporter reviews is the bytes
that leave -- not a description of them."""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
import sys
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePath, PureWindowsPath
from urllib.parse import quote, urlencode

from fastapi import APIRouter, HTTPException, Request

from .. import __version__
from ..config import Settings
from ..logsetup import LOG_FILE
from ..slskd_config import read_api_key, read_password, read_username, read_web_credentials
from ..tools import missing_helpers
from .guard import from_the_app
from .library import reveal_path
from .update import open_url

log = logging.getLogger(__name__)

REPORT_TO = "eyaldelarea@gmail.com"
# Gmail's own compose page: most reporters live in Gmail in a browser and never set up Mail, where a
# mailto: link would land on Mail's account setup instead of an email.
GMAIL_COMPOSE = "https://mail.google.com/mail/"
VIA = ("gmail", "mail")
REPORT_DIR = "bug-reports"
REPORT_PREFIX = "flackey-bug-report-"
KEEP_REPORTS = 3
UPDATE_HELPER_LOG = "flackey-update-helper.log"
# The current log, the one before it (a rotation can land a minute before the bug), and the update
# helper's own log, which is where a failed seamless update leaves its only trace.
LOG_NAMES = (LOG_FILE, f"{LOG_FILE}.1", UPDATE_HELPER_LOG)
# Per file, from the end: the newest lines are the ones a report is about, and a rotated log can be 2 MB.
LOG_TAIL_CHARS = 1_000_000
# Browsers stop somewhere past 8 KB, and one that hands mailto: to Gmail wraps the link in another URL,
# doubling it. The cap is on the *encoded* URL: a Hebrew letter or an emoji is 6-12 bytes encoded. The full
# text is in report.txt inside the zip, so a cut loses nothing.
URL_TEXT_CHARS = 1500
URL_MAX = 4000
SUBJECT_CHARS = 80
SCREENS = {"download": "Download", "library": "Library", "uploads": "Uploads", "settings": "Settings"}
# Shorter known values are skipped rather than redacted: a four-character Soulseek name like "beat"
# would take every "beat" in every track title with it, and a secret that short is not one worth hiding.
MIN_SECRET_CHARS = 4

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# A leading `+` is required: a bare run of digits is a Deezer track id or a timestamp far more often than
# it is a phone number. The second form is what `telegram.mask_phone` prints.
_PHONE = re.compile(r"\+\d[\d ().-]{6,}\d|\+\d{1,3} •+ •*\d{2}")
# A trailing `.` is allowed when no digit follows it, so an address that ends a sentence is still one.
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\d|\.\d)")
# Three colons at least, so a log timestamp's `12:00:00` is never one; `::` shortening is allowed, and the
# loopback `::1` has too few colons to match. The second form is a short `2001::1`, which needs its `::`.
_IPV6 = re.compile(r"(?<![\w:.])(?:[0-9A-Fa-f]{0,4}(?::[0-9A-Fa-f]{0,4}){3,7}|[0-9A-Fa-f]{1,4}::[0-9A-Fa-f]{1,4})"
                   r"(?![\w:])")
_KEEP_IPS = {"127.0.0.1", "0.0.0.0"}
# Any name that *ends* in one of these, so `telegram_api_hash=` and `access_token:` count too. The value is
# a whole quoted string when it is quoted, and an auth scheme word takes the credential after it along.
_KEY_VALUE = re.compile(r"(?i)(?<![A-Za-z0-9])([\w-]*(?:api[_-]?hash|api[_-]?key|password|passwd|token|secret|"
                        r"authorization))([\"']?\s*[:=]\s*)(\"[^\"\n]*\"|'[^'\n]*'|"
                        r"(?:(?:bearer|basic|digest|token)\s+)?[^\s\"',;}]+)")
# A password is the one value that may hold spaces unquoted (`password: my pass phrase`), so after one of
# these names the rest of the clause goes, not just its first word.
_PASSWORD_REST = re.compile(r"(?i)(?<![A-Za-z0-9])([\w-]*(?:password|passwd|passphrase))([\"']?\s*[:=]\s*)"
                            r"(?![\"'])([^\n,;}]+)")
# `scheme://user:pass@host`: the whole userinfo goes, before `_EMAIL` would take only `pass@host`.
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@]+@")
_OTHER_HOME = re.compile(r"/Users/[^/\s]+")
# Somebody's Windows home, in each spelling a log can hold it: `C:\Users\bob` as Windows prints it,
# `C:/Users/bob` as Python sometimes does, and `C:\\Users\\bob` inside a repr or a JSON string. Any
# drive and any case, because Windows paths are case-insensitive and nothing normalises them first.
_OTHER_WINDOWS_HOME = re.compile(r"(?i)(?<![\w])([a-z]:)(\\{1,2}|/)users\2[^\\/\s\"'<>|:*?]+")
_ANY_SEPARATOR = r"(?:\\{1,2}|/)"


class Redactor:
    """Takes out what identifies the reporter or unlocks their accounts, and leaves the track, artist
    and file names in -- those are what make a matching bug debuggable, and the reporter chose to keep
    them (issue #33).

    Known values first, patterns second: an exact secret is caught wherever it lands, including places no
    pattern would think to look, and the patterns catch what nobody told this class about."""

    def __init__(self, secrets: Iterable[str | None], home: PurePath | None = None,
                 users: Iterable[str | None] | None = None):
        """`home` and `users` default to this machine's. Tests hand in fixed ones so the result does
        not depend on who runs them: a `PureWindowsPath` home works on a Mac, a `PurePosixPath` one on
        Windows.

        On Windows the user name is not always the home folder's name -- a Microsoft-account sign-in
        makes the folder from the first five letters of the email, and a renamed account keeps its old
        folder -- so the sign-in name (`USERNAME`) is taken out as well as the folder's."""
        home = home if home is not None else Path.home()
        self.home = str(home)
        # A drive letter is how a Windows home announces itself, and derived from `home` rather than
        # `sys.platform` so a test of the Windows shape runs the same on a Mac.
        self._windows = bool(PureWindowsPath(self.home).drive)
        if users is None:
            users = [home.name, os.environ.get("USERNAME")] if self._windows else [home.name]
        known = {s.strip() for s in secrets if s and len(s.strip()) >= MIN_SECRET_CHARS}
        # Longest first, so a password that contains the username is not left half-replaced.
        self._known = [re.compile(r"(?<![\w])" + re.escape(s) + r"(?![\w])")
                       for s in sorted(known, key=len, reverse=True)]
        names = sorted({u.strip() for u in users if u and len(u.strip()) >= 3}, key=len, reverse=True)
        # Windows account names are case-insensitive, and a log prints whichever case the API returned.
        flags = re.IGNORECASE if self._windows else 0
        self._users = [re.compile(r"(?<![\w])" + re.escape(u) + r"(?![\w])", flags) for u in names]
        self._home_pattern = self._windows_home_pattern(self.home) if self._windows else None

    @staticmethod
    def _windows_home_pattern(home: str) -> re.Pattern:
        """This machine's home in any of the separators and cases `_OTHER_WINDOWS_HOME` accepts, and
        only as a whole folder name: `C:\\Users\\eyal` must not take the front off `C:\\Users\\eyalx`."""
        parts = [re.escape(p) for p in re.split(r"[\\/]+", home) if p]
        return re.compile(r"(?i)(?<![\w])" + _ANY_SEPARATOR.join(parts) + r"(?![^\\/\s\"'<>|:*?])")

    def __call__(self, text: str) -> str:
        # The home folder before the known values: a Soulseek name that equals the macOS user name would
        # otherwise turn `/Users/eyal/Music` into `/Users/<redacted>/Music` instead of the tidier `~/Music`.
        if self._home_pattern is not None:
            text = self._home_pattern.sub("~", text)
        else:
            text = text.replace(self.home, "~")
        text = _OTHER_HOME.sub("/Users/<user>", text)
        text = _OTHER_WINDOWS_HOME.sub(lambda m: f"{m.group(1)}{m.group(2)}Users{m.group(2)}<user>", text)
        for pattern in self._known:
            text = pattern.sub("<redacted>", text)
        for pattern in self._users:
            text = pattern.sub("<user>", text)
        text = _URL_USERINFO.sub(r"\1<redacted>@", text)
        text = _PASSWORD_REST.sub(_redact_value, text)
        text = _KEY_VALUE.sub(_redact_value, text)
        text = _EMAIL.sub("<email>", text)
        text = _PHONE.sub("<phone>", text)
        text = _IPV4.sub(lambda m: m.group(0) if m.group(0) in _KEEP_IPS else "<ip>", text)
        text = _IPV6.sub("<ip>", text)
        return text


def _redact_value(m: re.Match) -> str:
    value = m.group(3)
    quote = value[0] if value[:1] in "\"'" else ""
    return f"{m.group(1)}{m.group(2)}{quote}<redacted>{quote}"


def known_secrets(settings: Settings) -> list[str | None]:
    """Every credential flackey itself holds. `read_*` never raise, so a missing slskd.yml is just fewer
    values here rather than a report that cannot be built."""
    web = read_web_credentials(settings.data_dir) or (None, None)
    return [str(settings.telegram_api_id) if settings.telegram_api_id else None,
            settings.telegram_api_hash, settings.slskd_api_key, read_api_key(settings.data_dir),
            read_username(settings.data_dir), read_password(settings.data_dir), *web]


def display_summary() -> str:
    """How many displays, and whether one is external -- the window flicker (#31) turned on exactly
    that. Quartz rather than `NSScreen`: this runs on a server thread, and the CoreGraphics display
    list is safe to read from one. Anything short of an answer is "unknown", never a guess."""
    if sys.platform == "win32":
        return _windows_displays()
    if sys.platform != "darwin":
        return "unknown"
    try:
        from Quartz import (
            CGDisplayIsBuiltin,
            CGDisplayPixelsHigh,
            CGDisplayPixelsWide,
            CGGetActiveDisplayList,
        )
    except ImportError:
        return "unknown"
    try:
        err, ids, count = CGGetActiveDisplayList(16, None, None)
        if err or not count:
            return "unknown"
        parts = [f"{'built-in' if CGDisplayIsBuiltin(d) else 'external'} {CGDisplayPixelsWide(d)}×"
                 f"{CGDisplayPixelsHigh(d)}" for d in list(ids)[:count]]
    except Exception:
        log.debug("could not read the display list", exc_info=True)
        return "unknown"
    return f"{count} ({', '.join(parts)})"


_SM_CXSCREEN, _SM_CYSCREEN, _SM_CMONITORS = 0, 1, 80
_IMAGE_FILE_MACHINE_ARM64 = 0xAA64


def windows_display_text(count: int, width: int, height: int, dpi: int) -> str:
    """Scaling is the part that matters: the window's size and place are computed in it (see
    `desktop.window_origin`), and it is what differs between two machines at the same resolution."""
    return f"{count} (primary {width}×{height}, {round(dpi * 100 / 96)}% scale)"


def _windows_displays() -> str:
    try:
        import ctypes

        user32 = ctypes.windll.user32
        count = user32.GetSystemMetrics(_SM_CMONITORS)
        width, height = user32.GetSystemMetrics(_SM_CXSCREEN), user32.GetSystemMetrics(_SM_CYSCREEN)
        dpi = user32.GetDpiForSystem() or 96
    except Exception:
        log.debug("could not read the displays", exc_info=True)
        return "unknown"
    return windows_display_text(count, width, height, dpi) if count else "unknown"


def _windows_native_machine() -> int | None:
    """The CPU's own architecture, which `platform.machine()` does not give: an x64 Flackey emulated on an
    Arm PC is told AMD64. That emulation is why everything there is slow (a first launch took minutes in
    the Arm test VM), so a report that hid it would send the reader looking for a bug that isn't one."""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        process, native = wintypes.USHORT(), wintypes.USHORT()
        if not kernel32.IsWow64Process2(kernel32.GetCurrentProcess(), ctypes.byref(process), ctypes.byref(native)):
            return None
    except Exception:                    # IsWow64Process2 is Windows 10 1709 and later
        log.debug("could not read the native architecture", exc_info=True)
        return None
    return native.value


def os_summary() -> str:
    if sys.platform == "win32":
        # `platform.platform()` on Windows is `Windows-10-10.0.22631-SP0`: right, but not how anyone
        # says it. The build number stays because it is what tells Windows 10 from 11 on a Python
        # that reports both as release "10".
        release, version, _, _ = platform.win32_ver()
        machine = platform.machine()
        if _windows_native_machine() == _IMAGE_FILE_MACHINE_ARM64 and machine.upper() != "ARM64":
            machine += ", emulated on Arm"
        return f"Windows {release} (build {version}, {machine})"
    mac = platform.mac_ver()[0]
    return f"macOS {mac} ({platform.machine()})" if mac else platform.platform()


def file_browser() -> str:
    """What the reporter calls the window the zip is shown in."""
    return "File Explorer" if sys.platform == "win32" else "Finder"


def _build_label() -> str:
    """"(Mac app)" for the packaged app, "(Windows app)" for the Windows one, "(from source)" otherwise."""
    if not getattr(sys, "frozen", False):
        return " (from source)"
    return " (Windows app)" if sys.platform == "win32" else " (Mac app)"


@dataclass
class ClientContext:
    """What only the page knows. Every field is checked on the way in: it ends up in an email and a zip, so
    a value that is not the shape it should be is dropped rather than passed along."""
    screen: str | None = None
    window: str | None = None
    display: str | None = None

    @classmethod
    def parse(cls, body: dict) -> ClientContext:
        def size(v) -> str | None:
            try:
                w, h = int(v[0]), int(v[1])
            except (TypeError, ValueError, IndexError, KeyError):
                return None
            return f"{w}×{h}" if 0 < w < 100_000 and 0 < h < 100_000 else None

        screen = SCREENS.get(str(body.get("screen") or ""))
        window = size(body.get("window"))
        display = size(body.get("display"))
        ratio = body.get("pixel_ratio")
        if display and isinstance(ratio, (int, float)) and 0 < ratio < 10:
            display = f"{display} @{ratio:g}x"
        return cls(screen=screen, window=window, display=display)


@dataclass
class Report:
    summary: list[tuple[str, str]]
    files: dict[str, str] = field(default_factory=dict)  # name in the zip -> redacted text

    @property
    def log_lines(self) -> int:
        return sum(text.count("\n") for name, text in self.files.items() if name != "report.txt")


def _tail(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if len(text) <= LOG_TAIL_CHARS:
        return text
    cut = text[-LOG_TAIL_CHARS:]
    # Start on a whole line, and say that the start is not the start.
    return "[… earlier lines left out …]\n" + cut[cut.find("\n") + 1:]


def _connection_lines(settings: Settings, status) -> list[tuple[str, str]]:
    if not settings.telegram_configured:
        telegram = "not set up"
    elif not status.get("source_enabled", settings.source_enabled):
        telegram = "switched off"
    else:
        telegram = "connected" if status.get("telegram_authorized", True) else "signed out"
    if not settings.soulseek_enabled:
        soulseek = "not set up"
    else:
        provider = status.get("lossless_provider") or {}
        soulseek = "connected" if provider.get("status") == "ok" else (provider.get("status") or "not connected")
    return [("Deezer bot (Telegram)", telegram), ("Soulseek", soulseek)]


def build_report(settings: Settings, status, client: ClientContext, description: str = "",
                 steps: str = "", redact: Redactor | None = None,
                 now: datetime | None = None) -> Report:
    redact = redact or Redactor(known_secrets(settings))
    now = now or datetime.now(UTC)
    summary = [("Flackey", __version__ + _build_label()),
               ("System", os_summary()),
               ("Displays", display_summary())]
    if client.display:
        summary.append(("Screen size", client.display))
    if client.window:
        summary.append(("Window size", client.window))
    summary.append(("Was on", client.screen or "unknown"))
    summary += _connection_lines(settings, status)
    missing = missing_helpers()
    if missing:
        summary.append(("Missing helpers", ", ".join(missing)))
    summary = [(k, redact(v)) for k, v in summary]

    files: dict[str, str] = {}
    lines = [f"Flackey bug report, {now:%Y-%m-%d %H:%M UTC}", ""]
    lines += [f"{k}: {v}" for k, v in summary]
    if description.strip():
        lines += ["", "What went wrong:", redact(description.strip())]
    if steps.strip():
        lines += ["", "What they were doing:", redact(steps.strip())]
    files["report.txt"] = "\n".join(lines) + "\n"
    for name in LOG_NAMES:
        text = _tail(settings.data_dir / name)
        if text:
            files[name] = redact(text)
    return Report(summary=summary, files=files)


@dataclass
class Email:
    subject: str
    body: str
    url: str


def _compose_url(via: str, subject: str, body: str) -> str:
    # `quote`, not urlencode's default `quote_plus`: a mail app shows a `+` in a mailto: body as a `+`.
    if via == "mail":
        return f"mailto:{REPORT_TO}?" + urlencode({"subject": subject, "body": body}, quote_via=quote)
    return GMAIL_COMPOSE + "?" + urlencode({"view": "cm", "fs": "1", "to": REPORT_TO, "su": subject, "body": body},
                                           quote_via=quote)


def compose_email(report: Report, description: str, steps: str, zip_name: str, redact: Redactor,
                  via: str = "gmail") -> Email:
    """The email to the developer, written out, and the link that opens it: Gmail's compose page or a
    mailto: for whatever mail app the system uses. Only the summary and the reporter's own words go in; the
    log goes in the zip, which the reporter attaches by dragging. Each piece is redacted on its own rather
    than the finished body, whose email redaction would take the To address with it."""
    def cap(text: str, n: int) -> str:
        text = text.strip()
        return text if len(text) <= n else text[:n - 1].rstrip() + "…"

    first = next((ln for ln in description.strip().splitlines() if ln.strip()), "Something went wrong")
    details = "\n".join(f"{k}: {v}" for k, v in report.summary)
    # Shrink the reporter's text until the encoded URL fits; the whole of it is in report.txt regardless.
    limit = URL_TEXT_CHARS
    while True:
        subject = "Flackey bug: " + cap(redact(first), min(SUBJECT_CHARS, max(limit, 20)))
        parts = [cap(redact(description), limit)]
        if steps.strip():
            parts += ["", "What I was doing just before:", cap(redact(steps), limit)]
        parts += ["", "-- Details from Flackey --", details, "",
                  f"Attached: {zip_name} (the app log, with personal details removed).",
                  f"If it's not attached yet, drag it in from the {file_browser()} window Flackey opened."]
        body = "\n".join(parts)
        url = _compose_url(via, subject, body)
        if len(url) <= URL_MAX or limit <= 50:
            return Email(subject=subject, body=body, url=url)
        limit //= 2


def write_zip(report: Report, folder: Path, now: datetime | None = None) -> Path:
    now = now or datetime.now(UTC)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{REPORT_PREFIX}{now:%Y%m%d-%H%M%S}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, text in report.files.items():
            zf.writestr(name, text)
    # Old reports are the log as it was then, sitting in the data folder for nobody: keep the newest few,
    # so "Show the file again" still works for the last one and the folder does not grow forever.
    for old in sorted(folder.glob(f"{REPORT_PREFIX}*.zip"))[:-KEEP_REPORTS]:
        try:
            old.unlink()
        except OSError:
            log.debug("could not remove an old bug report %s", old, exc_info=True)
    return path


def latest_zip(folder: Path) -> Path | None:
    found = sorted(folder.glob(f"{REPORT_PREFIX}*.zip"))
    return found[-1] if found else None


def router(settings: Settings, status, opener: Callable[[Path], None] = reveal_path) -> APIRouter:
    r = APIRouter(prefix="/api/bug-report")
    folder = settings.data_dir / REPORT_DIR

    def build(body: dict) -> tuple[Report, Redactor, str, str]:
        description = str(body.get("description") or "")
        steps = str(body.get("steps") or "")
        redact = Redactor(known_secrets(settings))
        report = build_report(settings, status, ClientContext.parse(body), description, steps, redact)
        return report, redact, description, steps

    @r.post("/preview")
    async def preview(body: dict) -> dict:
        """Read-only: what would be sent. A POST only because the page's context is a body."""
        report, _, _, _ = await asyncio.to_thread(build, body)
        return {"summary": [[k, v] for k, v in report.summary],
                "files": [{"name": n, "text": t} for n, t in report.files.items()],
                "log_lines": report.log_lines}

    @r.post("")
    async def send(body: dict, request: Request) -> dict:
        from_the_app(request)
        if not str(body.get("description") or "").strip():
            raise HTTPException(400, "Say a few words about what went wrong first.")
        report, redact, description, steps = await asyncio.to_thread(build, body)
        try:
            path = await asyncio.to_thread(write_zip, report, folder)
        except OSError as e:
            log.warning("could not write the bug report: %s", e)
            raise HTTPException(500, "Could not save the report file. Try again.")
        via = body.get("via") if body.get("via") in VIA else "gmail"
        email = compose_email(report, description, steps, path.name, redact, via)
        log.info("bug report written: %s", path.name)
        # Both best effort: the response carries the address, the message and the file name, so a machine
        # where either cannot open (the browser build, a Linux box) can still send it by hand.
        try:
            opener(path)
        except Exception:
            log.warning("could not show the bug report in %s", file_browser(), exc_info=True)
        try:
            open_url(email.url)
        except Exception:
            log.warning("could not open an email for the bug report", exc_info=True)
        return {"ok": True, "file": path.name, "to": REPORT_TO,
                "subject": email.subject, "body": email.body}

    @r.post("/reveal")
    async def reveal(request: Request) -> dict:
        from_the_app(request)
        path = latest_zip(folder)
        if path is None:
            raise HTTPException(404, "The report file is no longer there. Send the report again.")
        opener(path)
        return {"ok": True}

    return r
