"""Report a bug (issue #33). The repository is public, so the redaction tests are the ones that matter:
each asserts that a value is *gone* from what would be sent, not merely that the redactor ran."""

import io
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from flackey import __version__
from flackey.config import Settings
from flackey.inbox import Inbox
from flackey.notify import MemoryNotifier
from flackey.store import Store
from flackey.telemetry.client import Telemetry
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
from tests.appclient import AppClient

# Pure, so it stays a Mac home with forward slashes when the suite runs on Windows.
HOME = PurePosixPath("/Users/someone")
APP = {"x-flackey-app": "1"}
NOT_FROM_APP = {"x-flackey-app": ""}


# ---- Redactor ---------------------------------------------------------------------------------

def test_known_secrets_are_removed_wherever_they_appear():
    r = Redactor(["s3cretHash", "dj-name", "hunter22"], home=HOME)
    out = r("logged in as dj-name with hunter22; hash s3cretHash;dj-name")
    assert "s3cretHash" not in out and "dj-name" not in out and "hunter22" not in out
    assert out.count("<redacted>") == 4


def test_slskds_own_web_username_is_not_a_secret_but_its_password_and_the_account_are(tmp_path):
    """slskd's web login is always called "flackey"; redacted as a secret it blanked the app's own name
    from every log line. The password beside it, the API key and the Soulseek account still go."""
    from flackey.slskd_config import read_web_credentials, write_credentials

    settings = Settings(_env_file=None, library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    key = write_credentials(settings.data_dir, "dj-name", "hunter22pass")
    web_user, web_password = read_web_credentials(settings.data_dir)
    assert web_user == "flackey"
    r = Redactor(report.known_secrets(settings), home=HOME)
    out = r(f"2026-10-22 INFO flackey.worker.core: logged in to slskd as {web_user}:{web_password} "
            f"with key {key}; soulseek dj-name / hunter22pass")
    assert "flackey.worker.core" in out and f"as {web_user}:" in out
    assert web_password not in out and key not in out and "dj-name" not in out and "hunter22pass" not in out


@pytest.mark.parametrize("line", [
    "GET /login?user=dj&pw=s3cr3tP%40ss%21 HTTP/1.1",
    "GET /login?x=s3cr3tP%40ss%21&y=1",
    "logged s3cr3tP%40ss%21 in a sentence",
    '{"pw": "s3cr3tP@ss!"}',
    "plain s3cr3tP@ss! here",
])
def test_a_known_secret_is_caught_in_its_encoded_forms(line):
    out = Redactor(["s3cr3tP@ss!"], home=HOME)(line)
    assert "s3cr3tP" not in out and "<redacted>" in out


def test_a_known_secret_is_caught_with_lower_case_percent_hex_and_json_escapes():
    r = Redactor(['pa"ss/wörd'], home=HOME)
    out = r(r'url pa%22ss%2fw%c3%b6rd json "pa\"ss/wörd" raw-json "pa\"ss/wörd"')
    assert "pa" not in out.replace("<redacted>", "")


def test_a_telegram_bot_token_is_removed_even_inside_a_url_path():
    r = Redactor([], home=HOME)
    token = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    out = r(f"HTTP Request: GET https://api.telegram.org/bot{token}/getMe \"HTTP/1.1 200 OK\"")
    assert token not in out and "123456789" not in out and "AAHdqTcv" not in out
    assert "api.telegram.org/bot<redacted>/getMe" in out
    assert token not in r(f"token {token} in a sentence")


@pytest.mark.parametrize("line, gone", [
    ("Cookie: session=abc123def456; theme=dark", "abc123def456"),
    ("set-cookie: sid=zzz999yyy; Path=/; HttpOnly", "zzz999yyy"),
    ("redirect to /home?session=q1w2e3r4t5y6", "q1w2e3r4t5y6"),
    ("sessionid=0f9e8d7c6b5a sent", "0f9e8d7c6b5a"),
])
def test_cookies_and_session_ids_are_removed(line, gone):
    assert gone not in Redactor([], home=HOME)(line)


def test_a_session_file_name_is_left_alone():
    """Only a bare `session=` value: the Telegram session *file* is named in logs and is not a secret."""
    line = "telegram_session=/data/owner.session"
    assert Redactor([], home=HOME)(line) == line


@pytest.mark.parametrize("text", [
    "error: login failed for s3cr3tPassw",                  # the stored message ends mid-secret
    'error: login failed for s3cr3tPassw\nnext line',
    '{"error_message": "login failed for s3cr3tPassw"}',   # ...and as request-N.json carries it
    "login failed for s3cr3tPa…",
    "login failed for s3cr3tPassword%2",                   # cut inside its URL spelling
])
def test_a_known_secret_cut_off_at_the_end_of_a_value_still_goes(text):
    """The worker keeps an exception's text to 500 characters, so a secret straddling the cut is stored as
    its first half; the whole-secret match would let that half through."""
    out = Redactor(["s3cr3tPassword!"], home=HOME)(text)
    assert "s3cr3tP" not in out and "<redacted>" in out


@pytest.mark.parametrize("text", [
    "s3cr3tPa and more after it",       # a prefix in the middle of a value is not a cut-off one
    "s3cr3t at the end",                # shorter than the eight characters that count
    "s3cr3tPasXword",                   # starts the same, then is something else
])
def test_only_a_real_cut_off_prefix_is_taken(text):
    assert Redactor(["s3cr3tPassword!"], home=HOME)(text) == text


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


WINDOWS_HOME = PureWindowsPath(r"C:\Users\eyald")


@pytest.mark.parametrize("spelling", [r"C:\Users\eyald\Music\DJ", "C:/Users/eyald/Music/DJ",
                                      r"C:\\Users\\eyald\\Music\\DJ", r"c:\users\EYALD\Music\DJ"])
def test_a_windows_home_becomes_a_tilde_however_it_is_spelled(spelling):
    """As Windows prints it, as Python sometimes does, inside a repr or JSON string, and in another case:
    each is the same folder, and every one of them names the reporter."""
    r = Redactor([], home=WINDOWS_HOME, users=["eyald"])
    out = r(f"library root: {spelling}")
    assert "eyald" not in out.lower()
    assert out.startswith("library root: ~") and out.endswith("DJ")


def test_a_windows_home_is_matched_as_a_whole_folder_name():
    r = Redactor([], home=WINDOWS_HOME, users=[])
    assert r(r"C:\Users\eyaldx\a") == r"C:\Users\<user>\a"


@pytest.mark.parametrize("path,want", [
    (r"D:\Users\Bob\x.flac", r"D:\Users\<user>\x.flac"),
    ("C:/Users/Bob/x.flac", "C:/Users/<user>/x.flac"),
    (r"C:\\Users\\Bob\\x.flac", r"C:\\Users\\<user>\\x.flac"),
])
def test_somebody_else_s_windows_home_loses_its_name(path, want):
    r = Redactor([], home=WINDOWS_HOME, users=[])
    assert r(path) == want


def test_the_windows_sign_in_name_goes_even_when_the_folder_is_named_otherwise(monkeypatch):
    """A Microsoft-account sign-in names the folder from the email's first five letters, so the folder
    name alone would leave the real account name in the log."""
    monkeypatch.setenv("USERNAME", "Eyal Delarea")
    r = Redactor([], home=WINDOWS_HOME)
    assert r("signed in as eyal delarea; folder eyald") == "signed in as <user>; folder <user>"


def test_a_mac_home_does_not_look_up_a_windows_sign_in_name(monkeypatch):
    monkeypatch.setenv("USERNAME", "somebody-else")
    assert Redactor([], home=HOME)("somebody-else was here") == "somebody-else was here"


def test_windows_is_described_by_release_and_build(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(report.platform, "win32_ver", lambda: ("11", "10.0.26100", "SP0", "Multiprocessor Free"))
    monkeypatch.setattr(report.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(report, "_windows_native_machine", lambda: 0x8664)
    assert report.os_summary() == "Windows 11 (build 10.0.26100, AMD64)"


def test_windows_on_arm_says_the_app_is_emulated(monkeypatch):
    """The x64 app on an Arm PC is told AMD64; the emulation is what makes everything there slow."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(report.platform, "win32_ver", lambda: ("11", "10.0.26200", "SP0", "Multiprocessor Free"))
    monkeypatch.setattr(report.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(report, "_windows_native_machine", lambda: 0xAA64)
    assert report.os_summary() == "Windows 11 (build 10.0.26200, AMD64, emulated on Arm)"


def test_windows_displays_carry_the_scaling():
    assert report.windows_display_text(1, 1024, 768, 96) == "1 (primary 1024×768, 100% scale)"
    assert report.windows_display_text(2, 2560, 1440, 144) == "2 (primary 2560×1440, 150% scale)"


@pytest.mark.skipif(sys.platform != "win32", reason="reads the real Windows displays")
def test_windows_display_summary_reads_the_machine():
    assert report.display_summary() != "unknown"


@pytest.mark.parametrize("system,frozen,label", [
    ("darwin", True, " (Mac app)"), ("win32", True, " (Windows app)"),
    ("darwin", False, " (from source)"), ("win32", False, " (from source)"),
])
def test_the_report_says_which_build_sent_it(tmp_path, monkeypatch, system, frozen, label):
    monkeypatch.setattr(report, "os_summary", lambda: "some system")
    monkeypatch.setattr(report, "missing_helpers", list)
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "platform", system)
    rep = build_report(settings_for(tmp_path), {}, ClientContext(), redact=Redactor([], home=HOME))
    assert dict(rep.summary)["Flackey"] == __version__ + label


@pytest.mark.parametrize("system,where", [
    ("darwin", "the Finder window Flackey opened"),
    ("win32", "the File Explorer window Flackey opened"),
    ("linux", "the window Flackey opened in your file manager"),
])
def test_the_email_names_the_window_the_zip_is_shown_in(tmp_path, monkeypatch, system, where):
    rep = build_report(settings_for(tmp_path), {}, ClientContext(), redact=Redactor([], home=HOME))
    monkeypatch.setattr(sys, "platform", system)
    email = compose_email(rep, "It broke", "", "z.zip", Redactor([], home=HOME))
    assert f"If it's not attached yet, drag it in from {where}." in email.body
    if system == "linux":
        assert "Finder" not in email.body


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


# ---- Security review 2026-11-24 (L3/S15): more of what a log can carry ----------------------------

def test_the_api_token_and_the_usage_stats_id_are_known_secrets(tmp_path):
    settings = settings_for(tmp_path)
    settings.telemetry_install_id = "3f2b8c1e-7d4a-4e2b-9c1d-5a6b7c8d9e0f"
    token = "T" * 20 + "okenValue_-1234567890"
    out = Redactor(report.known_secrets(settings, api_token=token), home=HOME)(
        f"GET /#t={token} for install 3f2b8c1e-7d4a-4e2b-9c1d-5a6b7c8d9e0f")
    assert token not in out and "3f2b8c1e" not in out


def test_the_report_route_redacts_this_launchs_api_token(app):
    client, settings, _, _ = app
    token = client.app.state.api_token
    (settings.data_dir / "flackey.log").write_text(f"2026-11-24 INFO opened http://127.0.0.1:8765/#t={token}\n")
    body = client.post("/api/bug-report/preview", json={}).text
    assert token not in body and "<redacted>" in body


@pytest.mark.parametrize("line, gone", [
    ("flackey_session=abc123def456ghi", "abc123def456ghi"),
    ("flackey_session_8765=abc123def456ghi; Path=/api", "abc123def456ghi"),
    ("csrf_session=zz99yy88xx77", "zz99yy88xx77"),
])
def test_a_named_session_cookie_value_goes(line, gone):
    assert gone not in Redactor([], home=HOME)(line)


@pytest.mark.parametrize("line, gone", [
    ("secret_key=s3kr1tvalue", "s3kr1tvalue"),
    ('"private_key": "abcdef123456"', "abcdef123456"),
    ("ACCESS-KEY: AKIA1234567890", "AKIA1234567890"),
    ("sent Bearer eyJhbGciOiJIUzI1NiJ9.abc.def upstream", "eyJhbGciOiJIUzI1NiJ9"),
])
def test_more_key_value_and_bearer_shapes_go(line, gone):
    assert gone not in Redactor([], home=HOME)(line)


def test_a_camelot_key_is_not_a_secret():
    line = "tagged key=8A bpm=128 (Original Mix)"
    assert Redactor([], home=HOME)(line) == line


@pytest.mark.parametrize("spelling", ["/users/someone/Music/DJ", "/USERS/SOMEONE/Music/DJ"])
def test_the_mac_home_in_another_case_is_still_the_home(spelling):
    """APFS ignores case, so a typed path can name the home folder in any case."""
    out = Redactor([], home=HOME)(f"library root: {spelling}")
    assert out == "library root: ~/Music/DJ"


def test_the_mac_user_name_goes_in_any_case_and_only_as_a_whole_name():
    r = Redactor([], home=HOME)
    assert r("owner SomeOne opened it") == "owner <user> opened it"
    assert r("/Users/someonex/a") == "/Users/<user>/a"
    assert r("/users/another/x") == "/Users/<user>/x"


@pytest.mark.parametrize("path, want", [
    ("/home/bob/Music/x.flac", "/home/<user>/Music/x.flac"),
    ("/Volumes/My Drive/Music/x.flac", "/Volumes/<drive>/Music/x.flac"),
    ("PosixPath('/Volumes/DJ SSD/Music')", "PosixPath('/Volumes/<drive>/Music')"),
    ("drive /Volumes/Samsung_T7 is gone", "drive /Volumes/<drive> is gone"),
    (r"\\nas-01\music\x.flac", r"\\<server>\music\x.flac"),
    (r'"\\\\nas-01\\music"', r'"\\\\<server>\\music"'),
    (r"C:\Users\<user>\OneDrive - Contoso Ltd\Music", r"C:\Users\<user>\OneDrive - <org>\Music"),
])
def test_drive_server_and_company_names_in_paths_go(path, want):
    assert Redactor([], home=HOME)(path) == want


@pytest.mark.parametrize("line, want", [
    ("connected to Eyals-MacBook-Pro.local:5030", "connected to <host>.local:5030"),
    ("http://nas.local/music", "http://<host>.local/music"),
    ("~/.local/share/flackey stays", "~/.local/share/flackey stays"),
    ("module flackey.localfile stays", "module flackey.localfile stays"),
    ("mail bob.smith@Bobs-MacBook-Pro.local now", "mail <email> now"),
    ("http://bob:hunter2@Bobs-MacBook-Pro.local/x", "http://<redacted>@<host>.local/x"),
])
def test_a_local_network_host_name_goes(line, want):
    """A Mac's Bonjour name is usually its owner's name: `Eyals-MacBook-Pro.local`."""
    assert Redactor([], home=HOME)(line) == want


@pytest.mark.parametrize("phone", ["054-123-4567", "054.123.4567", "054 123 4567", "(555) 123-4567"])
def test_a_phone_number_without_a_plus_goes_in_its_usual_shapes(phone):
    assert Redactor([], home=HOME)(f"call {phone} now") == "call <phone> now"


def test_numbers_that_are_not_phones_are_left_alone():
    """Timestamps, Deezer ids, durations, sizes, an ISRC and a version: none of them may become <phone>."""
    line = ("2026-11-24 12:00:00,123 deezer 3135556 isrc USRC17607839 took 1234 ms, 58309223 bytes, "
            "2026-11-24T12:00:00Z v0.8.1 1/2/2026 req#42 12345678901 3 of 10")
    assert Redactor([], home=HOME)(line) == line


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


def test_the_report_tells_an_unreachable_telegram_from_a_signed_out_one(tmp_path: Path):
    settings = settings_for(tmp_path)
    seed_logs(settings)
    rep = build_report(settings, {"telegram_authorized": False, "telegram_unreachable": True},
                       ClientContext(screen="Settings"), "", "")
    assert dict(rep.summary)["Deezer bot (Telegram)"] == "not reachable"


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


@pytest.fixture
def app(tmp_path: Path, monkeypatch):
    settings = settings_for(tmp_path)
    seed_logs(settings)
    store = Store(settings.db_path)
    worker = Worker(store, DummySource(), MemoryNotifier(), settings)
    revealed: list[Path] = []
    opened: list[str] = []
    monkeypatch.setattr(report, "open_url", opened.append)
    client = AppClient(create_app(store, worker, Inbox(store), settings, opener=revealed.append))
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


@pytest.mark.parametrize(("via", "opens"), [(None, "https://mail.google.com/mail/?view=cm"),
                                           ("mail", f"mailto:{report.REPORT_TO}?")])
def test_sending_writes_the_zip_reveals_it_and_opens_the_email(app, via, opens):
    client, settings, revealed, opened = app
    res = client.post("/api/bug-report", json={"description": "Window flickers", "screen": "library", "via": via},
                      headers=APP)
    assert res.status_code == 200
    out = res.json()
    zip_path = settings.data_dir / "bug-reports" / out["file"]
    assert revealed == [zip_path] and len(opened) == 1 and opened[0].startswith(opens)
    assert out["to"] == report.REPORT_TO and out["subject"] == "Flackey bug: Window flickers"
    assert "Was on: Library" in out["body"]
    with zipfile.ZipFile(io.BytesIO(zip_path.read_bytes())) as zf:
        assert "Window flickers" in zf.read("report.txt").decode()


def test_sending_needs_the_app_header_and_a_description(app):
    client, _, revealed, opened = app
    assert client.post("/api/bug-report", json={"description": "x"}, headers=NOT_FROM_APP).status_code == 403
    res = client.post("/api/bug-report", json={"description": "   "}, headers=APP)
    assert res.status_code == 400 and "what went wrong" in res.json()["detail"]
    assert revealed == [] and opened == []


def test_an_email_that_will_not_open_still_returns_the_message(app, monkeypatch):
    client, _, _, _ = app

    def refuse(url):
        raise OSError("no browser")
    monkeypatch.setattr(report, "open_url", refuse)
    res = client.post("/api/bug-report", json={"description": "x"}, headers=APP)
    assert res.status_code == 200 and res.json()["to"] == report.REPORT_TO and res.json()["body"].startswith("x")


def test_reveal_shows_the_newest_report_or_says_it_is_gone(app):
    client, settings, revealed, _ = app
    assert client.post("/api/bug-report/reveal", headers=APP).status_code == 404
    out = client.post("/api/bug-report", json={"description": "x"}, headers=APP).json()
    revealed.clear()
    assert client.post("/api/bug-report/reveal", headers=NOT_FROM_APP).status_code == 403
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


@pytest.mark.parametrize("line, gone", [
    ("password: my pass phrase here", "pass phrase"),
    ("slskd_password=two words", "two words"),
    ("fetching http://dj:hunter2@proxy.example:8080/x", "hunter2"),
    ("fetching https://dj@host.example/x", "dj@"),
])
def test_passwords_with_spaces_and_url_logins_are_redacted(line, gone):
    out = Redactor([], home=HOME)(line)
    assert gone not in out and "<redacted>" in out


def test_the_install_secret_never_reaches_a_bug_report(tmp_path: Path):
    """The usage-stats install secret proves ownership of the install ID to the collector: a report that
    carried it would let whoever reads the report delete or impersonate that install's stats."""
    import zipfile as _zip

    from flackey.telemetry.client import new_secret

    settings = settings_for(tmp_path)
    settings.telemetry_install_secret = new_secret()
    secret = settings.telemetry_install_secret
    seed_logs(settings)
    with (settings.data_dir / "flackey.log").open("a", encoding="utf-8") as f:
        f.write(f"2026-09-29 12:00:04.000 DEBUG   flackey.x: body {{\"install_secret\": \"{secret}\"}} raw {secret}\n")
    rep = build_report(settings, {}, ClientContext(), f"my secret is {secret}", "")
    assert secret not in "".join(rep.files.values())
    path = write_zip(rep, tmp_path / "out")
    with _zip.ZipFile(path) as z:
        assert all(secret.encode() not in z.read(n) for n in z.namelist())


# ---- sending to the Flackey server (issue #279) -----------------------------------------------

COLLECTOR = "https://collector.test"


class Collector:
    """A stand-in for the collector's /v1/bug-report: answers what the test says, records what it got."""

    def __init__(self, status: int = 201, body: dict | None = None, fail: Exception | None = None):
        self.status, self.fail, self.seen = status, fail, []
        self.body = {"stored": True, "id": "0acbd8bf-b458-4e00-b4bf-a4314d24060e"} if body is None else body

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.fail is not None:
            raise self.fail
        return httpx.Response(self.status, json=self.body)


@pytest.fixture
def server(monkeypatch):
    """Point the report at a fake collector with a release key, the way a packaged build is."""
    def use(collector: Collector, target=(COLLECTOR, "release-key")):
        monkeypatch.setattr(report, "collector_target", lambda: target)
        monkeypatch.setattr(report, "UPLOAD_TRANSPORT", httpx.MockTransport(collector))
        return collector
    return use


def test_preview_says_whether_the_report_can_go_to_the_server(app, server, monkeypatch):
    client, _, _, _ = app
    monkeypatch.setattr(report, "collector_target", lambda: None)
    assert client.post("/api/bug-report/preview", json={}).json()["server"] is False
    server(Collector())
    assert client.post("/api/bug-report/preview", json={}).json()["server"] is True


def test_send_uploads_the_zip_it_saved_and_nothing_else(app, server):
    client, settings, revealed, opened = app
    got = server(Collector())
    res = client.post("/api/bug-report/upload", json={"description": "Window flickers", "screen": "library"},
                      headers=APP)
    assert res.status_code == 200
    out = res.json()
    assert out["sent"] is True and out["reference"] == "0acbd8bf"
    (req,) = got.seen
    assert str(req.url) == f"{COLLECTOR}/v1/bug-report" and req.method == "POST"
    assert req.headers["content-type"] == "application/zip"
    assert req.headers["x-flackey-key"] == "release-key"
    saved = (settings.data_dir / "bug-reports" / out["file"]).read_bytes()
    assert req.content == saved, "what leaves is the zip the reporter can still open on disk"
    with zipfile.ZipFile(io.BytesIO(req.content)) as zf:
        assert "Window flickers" in zf.read("report.txt").decode()
        assert "0123456789abcdef" not in "".join(zf.read(n).decode() for n in zf.namelist())
    assert revealed == [] and opened == [], "no email, no Finder window: it went"


def test_the_upload_carries_no_usage_stats_id_or_secret(app, server):
    client, settings, _, _ = app
    settings.telemetry_install_id = "5b0e2c1a-7d3f-4c1e-9a2b-1c2d3e4f5a6b"
    settings.telemetry_install_secret = "s" * 43
    got = server(Collector())
    client.post("/api/bug-report/upload", json={"description": "x"}, headers=APP)
    (req,) = got.seen
    wire = req.content + b"".join(k + v for k, v in req.headers.raw)
    with zipfile.ZipFile(io.BytesIO(req.content)) as zf:
        wire += b"".join(zf.read(n) for n in zf.namelist())
    assert b"5b0e2c1a" not in wire and b"s" * 43 not in wire
    assert set(req.headers.keys()) <= {"host", "accept", "accept-encoding", "connection", "user-agent",
                                       "content-length", "content-type", "x-flackey-key"}


@pytest.mark.parametrize(("collector", "why"), [
    (Collector(status=429, body={"error": "rate_limited"}), "HTTP 429"),
    (Collector(status=503, body={"error": "stopped"}), "HTTP 503"),
    (Collector(status=403, body={"error": "bad_key"}), "HTTP 403"),
    (Collector(status=404, body={}), "HTTP 404"),
    (Collector(status=202, body={"stop": True}), "did not keep"),
    (Collector(status=201, body={"stored": False}), "did not keep"),
    (Collector(fail=httpx.ConnectError("no route")), "Couldn't reach"),
])
def test_anything_but_stored_offers_email_and_keeps_the_report(app, server, collector, why):
    client, settings, revealed, opened = app
    got = server(collector)
    out = client.post("/api/bug-report/upload", json={"description": "x"}, headers=APP).json()
    assert out["sent"] is False and why in out["error"]
    assert (settings.data_dir / "bug-reports" / out["file"]).exists(), "the reporter never loses the report"
    assert len(got.seen) == 1, "never retried behind the reporter's back"
    assert revealed == [] and opened == [], "email is the reporter's choice, offered, not opened"


def test_with_no_server_nothing_is_sent_and_email_is_offered(app, server):
    client, _, _, _ = app
    got = server(Collector(), target=None)
    out = client.post("/api/bug-report/upload", json={"description": "x"}, headers=APP).json()
    assert out["sent"] is False and got.seen == []


def test_upload_needs_the_app_header_and_a_description(app, server):
    client, _, _, _ = app
    got = server(Collector())
    assert client.post("/api/bug-report/upload", json={"description": "x"}, headers=NOT_FROM_APP).status_code == 403
    assert client.post("/api/bug-report/upload", json={"description": " "}, headers=APP).status_code == 400
    assert got.seen == []


def _telemetry(tmp_path: Path, **kw) -> Telemetry:
    return Telemetry(tmp_path, version="0.8.2", channel="packaged",
                     platform_fields={"os": "macos", "os_major": "15", "arch": "arm64"}, **kw)


def test_the_server_is_the_release_builds_collector_whether_or_not_stats_are_on(tmp_path: Path, monkeypatch):
    """Pressing Send is the consent for that one report (issue #279): the stats opt-in plays no part."""
    from flackey import telemetry as usage

    monkeypatch.setattr(usage, "_current", _telemetry(tmp_path, enabled=False, built_url=COLLECTOR, key="k"))
    assert report.collector_target() == (COLLECTOR, "k")
    monkeypatch.setattr(usage, "_current", _telemetry(tmp_path, enabled=False, url="http://127.0.0.1:8099"))
    assert report.collector_target() == ("http://127.0.0.1:8099", None)


@pytest.mark.parametrize("kw", [
    {"mode": "off", "built_url": COLLECTOR},          # FLACKEY_TELEMETRY=off: nothing leaves for us
    {"mode": "local", "built_url": COLLECTOR},        # the local demo never sends
    {},                                               # a checkout or a build with no collector
    {"built_url": "http://not-https.example"},        # refused by build_url
    {"url": "http://someone-else.example"},           # the environment may only name this computer
])
def test_no_server_when_there_is_none_or_sending_is_switched_off(tmp_path: Path, monkeypatch, kw):
    from flackey import telemetry as usage

    monkeypatch.setattr(usage, "_current", _telemetry(tmp_path, **kw))
    assert report.collector_target() is None


def test_no_server_before_the_stats_client_exists(monkeypatch):
    from flackey import telemetry as usage

    monkeypatch.setattr(usage, "_current", None)
    assert report.collector_target() is None


def test_a_report_too_big_for_the_server_loses_its_oldest_log_lines_and_the_preview_shows_it(app, monkeypatch):
    """The preview and the upload are one build: what the reporter reviews is what fits and what goes."""
    import random

    client, settings, _, _ = app
    monkeypatch.setattr(report, "UPLOAD_MAX", 20_000)
    rnd = random.Random(7)
    lines = "".join(f"2026-09-29 12:{i // 60:02}:{i % 60:02} INFO flackey.worker: {rnd.getrandbits(128):032x}\n"
                    for i in range(4000))
    (settings.data_dir / "flackey.log").write_text(lines + "the newest line\n", encoding="utf-8")
    preview = client.post("/api/bug-report/preview", json={"description": "x"}).json()
    log = next(f["text"] for f in preview["files"] if f["name"] == "flackey.log")
    assert log.startswith("[… earlier lines left out …]\n") and log.endswith("the newest line\n")
    rep = build_report(settings, {}, ClientContext(), "x")
    assert len(report.zip_bytes(report.fit_for_upload(rep))) <= 20_000
