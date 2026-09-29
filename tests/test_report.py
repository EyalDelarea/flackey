"""Report a bug (issue #33). The repository is public, so the redaction tests are the ones that matter:
each asserts that a value is *gone* from what would be sent, not merely that the redactor ran."""

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from flackey import __version__
from flackey.config import Settings
from flackey.inbox import Inbox
from flackey.notify import MemoryNotifier
from flackey.store import Store
from flackey.web import create_app, report
from flackey.web.report import (
    KEEP_REPORTS,
    URL_TEXT_CHARS,
    ClientContext,
    Redactor,
    build_report,
    compose_email,
    write_zip,
)
from flackey.worker import Worker

HOME = Path("/Users/someone")
APP = {"x-flackey-app": "1"}


# ---- Redactor ---------------------------------------------------------------------------------

def test_known_secrets_are_removed_wherever_they_appear():
    r = Redactor(["s3cretHash", "dj-name", "hunter22"], home=HOME)
    out = r("logged in as dj-name with hunter22; hash s3cretHash;dj-name")
    assert "s3cretHash" not in out and "dj-name" not in out and "hunter22" not in out
    assert out.count("<redacted>") == 4


def test_a_secret_is_matched_whole_not_inside_another_word():
    r = Redactor(["beatz"], home=HOME)
    assert r("Beatzone - beatzilla (beatz edit)") == "Beatzone - beatzilla (<redacted> edit)"


def test_short_and_empty_secrets_are_ignored_rather_than_eating_the_log():
    r = Redactor([None, "", "abc", "  "], home=HOME)
    assert r("abc 1 2 3") == "abc 1 2 3"


def test_the_home_folder_becomes_a_tilde_and_the_user_name_goes_too():
    r = Redactor([], home=HOME)
    assert r("library root: /Users/someone/Music/DJ") == "library root: ~/Music/DJ"
    assert r("owner someone opened it") == "owner <user> opened it"
    assert r("/Users/another/Desktop/x.flac") == "/Users/<user>/Desktop/x.flac"


def test_key_value_secrets_are_redacted_by_shape():
    r = Redactor([], home=HOME)
    out = r('api_hash=abc123 "password": "p4ss" Authorization: Bearer xyz.789 token=t0k')
    for leaked in ("abc123", "p4ss", "xyz.789", "t0k"):
        assert leaked not in out
    assert "api_hash=<redacted>" in out


def test_emails_phones_and_public_addresses_are_removed():
    r = Redactor([], home=HOME)
    out = r("mail a.b+c@example.co.uk phone +972 54-123-4567 masked +97 •••• ••67 "
            "peer 84.12.3.4:2234 v6 2a02:6b8:0:1::1 and fe80::1:2:3")
    for leaked in ("example.co.uk", "123-4567", "••67", "84.12.3.4", "2a02", "fe80"):
        assert leaked not in out
    assert out.count("<phone>") == 2 and out.count("<ip>") == 3


def test_what_makes_a_log_useful_is_left_alone():
    r = Redactor(["s3cretHash"], home=HOME)
    line = ("2026-09-29 12:00:00.123 INFO    flackey.worker: request 42 deezer 3135556 "
            "Astral Projection - Into The Void (Original Mix) 320kbps -> 127.0.0.1:5030 ::1 v1.2.3")
    assert r(line) == line


# ---- ClientContext ----------------------------------------------------------------------------

def test_client_context_keeps_known_values_and_drops_anything_else():
    ok = ClientContext.parse({"screen": "library", "window": [1100, 720], "display": [3440, 1440], "pixel_ratio": 2})
    assert ok == ClientContext(screen="Library", window="1100×720", display="3440×1440 @2x")
    junk = ClientContext.parse({"screen": "<script>", "window": ["a", 1], "display": [-1, 9], "pixel_ratio": "x"})
    assert junk == ClientContext()


# ---- building and sending ---------------------------------------------------------------------

def settings_for(tmp_path: Path) -> Settings:
    return Settings(_env_file=None, telegram_api_id=12345678, telegram_api_hash="0123456789abcdef",
                    slskd_api_key="slskd-key-9876", library_root=tmp_path / "lib", data_dir=tmp_path / "data")


def seed_logs(settings: Settings) -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "flackey.log").write_text(
        "2026-09-29 12:00:00.000 INFO    flackey.logsetup: data dir: " + str(Path.home()) + "/Library/x\n"
        "2026-09-29 12:00:01.000 DEBUG   flackey.telegram: keys 12345678 0123456789abcdef\n"
        "2026-09-29 12:00:02.000 DEBUG   flackey.slskd: key slskd-key-9876 peer 84.12.3.4\n"
        "2026-09-29 12:00:03.000 INFO    flackey.worker: filed Artist - Title (Extended Mix)\n",
        encoding="utf-8")
    (settings.data_dir / "flackey.log.1").write_text("older line\n", encoding="utf-8")


def test_the_report_carries_redacted_logs_and_a_summary(tmp_path: Path):
    settings = settings_for(tmp_path)
    seed_logs(settings)
    rep = build_report(settings, {"telegram_authorized": False}, ClientContext(screen="Settings"),
                       "It broke, mail me at x@y.com", "Pressed Download")
    everything = "".join(rep.files.values())
    for leaked in ("12345678", "0123456789abcdef", "slskd-key-9876", "84.12.3.4", "x@y.com", str(Path.home())):
        assert leaked not in everything
    assert "Artist - Title (Extended Mix)" in rep.files["flackey.log"]
    assert set(rep.files) == {"report.txt", "flackey.log", "flackey.log.1"}
    summary = dict(rep.summary)
    assert summary["Flackey"].startswith(__version__)
    assert summary["Was on"] == "Settings"
    assert summary["Deezer bot (Telegram)"] == "signed out"
    assert "Pressed Download" in rep.files["report.txt"]
    assert rep.log_lines == 5


def test_a_long_log_is_cut_from_the_front_on_a_line_boundary(tmp_path: Path, monkeypatch):
    settings = settings_for(tmp_path)
    settings.data_dir.mkdir(parents=True)
    monkeypatch.setattr(report, "LOG_TAIL_CHARS", 30)
    (settings.data_dir / "flackey.log").write_text("".join(f"line {i:03}\n" for i in range(20)), encoding="utf-8")
    text = build_report(settings, {}, ClientContext()).files["flackey.log"]
    assert text.startswith("[… earlier lines left out …]\nline 017\n") and text.endswith("line 019\n")


def test_the_gmail_link_opens_a_written_email_to_the_developer(tmp_path: Path):
    settings = settings_for(tmp_path)
    rep = build_report(settings, {}, ClientContext(screen="Download"))
    email = compose_email(rep, "\n  QR never shows\n" + "x" * 5000, "Opened setup", "flackey-bug-report-1.zip",
                          Redactor([], home=HOME))
    parts = urlsplit(email.url)
    assert parts.netloc == "mail.google.com" and parts.path == "/mail/"
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert q["view"] == "cm" and q["to"] == report.REPORT_TO
    assert q["su"] == email.subject == "Flackey bug: QR never shows"
    assert q["body"] == email.body
    assert "x" * (URL_TEXT_CHARS - 20) in email.body and "x" * URL_TEXT_CHARS not in email.body
    assert "What I was doing just before:\nOpened setup" in email.body
    assert "Was on: Download" in email.body and "flackey-bug-report-1.zip" in email.body
    assert len(email.url) <= report.URL_MAX


def test_the_mailto_link_uses_percent_twenty_not_plus(tmp_path: Path):
    rep = build_report(settings_for(tmp_path), {}, ClientContext())
    email = compose_email(rep, "Window goes blank", "", "flackey-bug-report-1.zip", Redactor([], home=HOME),
                          via="mail")
    assert email.url.startswith(f"mailto:{report.REPORT_TO}?subject=Flackey%20bug%3A%20Window%20goes%20blank&body=")
    assert "+" not in email.url
    assert "What I was doing" not in email.body


def test_the_developer_address_survives_the_redaction_of_emails(tmp_path: Path):
    rep = build_report(settings_for(tmp_path), {}, ClientContext())
    email = compose_email(rep, "mail me at me@example.com", "", "z.zip", Redactor([], home=HOME))
    assert "me@example.com" not in email.url
    assert parse_qs(urlsplit(email.url).query)["to"] == [report.REPORT_TO]


def test_old_report_zips_are_pruned(tmp_path: Path):
    rep = build_report(settings_for(tmp_path), {}, ClientContext())
    for minute in range(KEEP_REPORTS + 2):
        write_zip(rep, tmp_path / "out", now=datetime(2026, 9, 29, 12, minute, tzinfo=UTC))
    kept = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert kept == [f"flackey-bug-report-20260929-12{m:02}00.zip" for m in range(2, KEEP_REPORTS + 2)]


# ---- the endpoints ----------------------------------------------------------------------------

class DummySource:
    name = "x"

    async def search(self, q):
        return []

    async def fetch(self, c, d):
        raise NotImplementedError


class DummyCatalog:
    async def search(self, q):
        return []


@pytest.fixture
def app(tmp_path: Path, monkeypatch):
    settings = settings_for(tmp_path)
    seed_logs(settings)
    store = Store(settings.db_path)
    worker = Worker(store, DummySource(), DummyCatalog(), MemoryNotifier(), settings)
    revealed: list[Path] = []
    opened: list[str] = []
    monkeypatch.setattr(report, "open_url", opened.append)
    client = TestClient(create_app(store, worker, Inbox(store), settings, opener=revealed.append))
    return client, settings, revealed, opened


def test_preview_shows_exactly_what_the_zip_will_hold(app):
    client, settings, _, _ = app
    body = {"description": "It broke", "screen": "download"}
    preview = client.post("/api/bug-report/preview", json=body).json()
    assert ["Was on", "Download"] in preview["summary"]
    assert preview["log_lines"] == 5
    sent = client.post("/api/bug-report", json=body, headers=APP).json()
    with zipfile.ZipFile(settings.data_dir / "bug-reports" / sent["file"]) as zf:
        zipped = {n: zf.read(n).decode() for n in zf.namelist()}
    shown = {f["name"]: f["text"] for f in preview["files"]}
    assert zipped.keys() == shown.keys()
    # report.txt carries a timestamp to the minute; the logs are the part that has to match to the byte.
    assert {n: t for n, t in zipped.items() if n != "report.txt"} == {n: t for n, t in shown.items() if n != "report.txt"}
    assert "0123456789abcdef" not in "".join(zipped.values())


def test_sending_writes_the_zip_reveals_it_and_opens_the_email(app):
    client, settings, revealed, opened = app
    res = client.post("/api/bug-report", json={"description": "Window flickers", "screen": "library"}, headers=APP)
    assert res.status_code == 200
    out = res.json()
    zip_path = settings.data_dir / "bug-reports" / out["file"]
    assert revealed == [zip_path] and opened == [out["url"]]
    assert out["url"].startswith("https://mail.google.com/mail/?view=cm")
    assert out["to"] == report.REPORT_TO and out["subject"] == "Flackey bug: Window flickers"
    assert "Was on: Library" in out["body"]
    with zipfile.ZipFile(io.BytesIO(zip_path.read_bytes())) as zf:
        assert "Window flickers" in zf.read("report.txt").decode()


def test_sending_needs_the_app_header_and_a_description(app):
    client, _, revealed, opened = app
    assert client.post("/api/bug-report", json={"description": "x"}).status_code == 403
    res = client.post("/api/bug-report", json={"description": "   "}, headers=APP)
    assert res.status_code == 400 and "what went wrong" in res.json()["detail"]
    assert revealed == [] and opened == []


def test_a_browser_that_will_not_open_still_returns_the_link(app, monkeypatch):
    client, _, _, _ = app

    def refuse(url):
        raise OSError("no browser")
    monkeypatch.setattr(report, "open_url", refuse)
    res = client.post("/api/bug-report", json={"description": "x"}, headers=APP)
    assert res.status_code == 200 and res.json()["url"].startswith("https://mail.google.com/")


def test_the_mail_app_route_opens_a_mailto_link(app):
    client, _, _, opened = app
    out = client.post("/api/bug-report", json={"description": "x", "via": "mail"}, headers=APP).json()
    assert opened == [out["url"]] and out["url"].startswith(f"mailto:{report.REPORT_TO}?")


def test_reveal_shows_the_newest_report_or_says_it_is_gone(app):
    client, settings, revealed, _ = app
    assert client.post("/api/bug-report/reveal", headers=APP).status_code == 404
    out = client.post("/api/bug-report", json={"description": "x"}, headers=APP).json()
    revealed.clear()
    assert client.post("/api/bug-report/reveal").status_code == 403
    assert client.post("/api/bug-report/reveal", headers=APP).json() == {"ok": True}
    assert revealed == [settings.data_dir / "bug-reports" / out["file"]]


def test_prefixed_keys_auth_schemes_and_quoted_values_are_redacted_whole():
    r = Redactor([], home=HOME)
    out = r("telegram_api_hash=abc123 SLSKD_API_KEY=k1 access_token: t2 Authorization: Basic dXNlcjpwYXNz "
            "password='a b c' \"password\": \"p 4\"")
    for leaked in ("abc123", "k1", "t2", "dXNlcjpwYXNz", "a b c", "p 4"):
        assert leaked not in out
    assert "password='<redacted>'" in out


def test_addresses_at_a_sentence_end_and_short_ipv6_are_caught():
    r = Redactor([], home=HOME)
    assert r("connected to 84.12.3.4.") == "connected to <ip>."
    assert r("peer 2001::1 up") == "peer <ip> up"
    assert r("build 1.2.3.4.5 at 12:00:00") == "build 1.2.3.4.5 at 12:00:00"


@pytest.mark.parametrize("via", ["gmail", "mail"])
def test_the_url_stays_short_even_for_text_that_encodes_large(tmp_path: Path, via: str):
    rep = build_report(settings_for(tmp_path), {}, ClientContext())
    hebrew = "החלון מהבהב כשאני גורר אותו למסך השני 🎧 " * 60
    email = compose_email(rep, hebrew, hebrew, "flackey-bug-report-1.zip", Redactor([], home=HOME), via)
    assert len(email.url) <= report.URL_MAX
    assert email.body.startswith("החלון") and "…" in email.body
