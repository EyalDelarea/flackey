from pathlib import Path

import pytest
import yaml

from flackey.slskd_config import (
    SlskdConfigError,
    config_path,
    read_api_key,
    read_password,
    read_username,
    write_credentials,
)


def test_config_path_is_under_data_dir_slskd(tmp_path: Path):
    assert config_path(tmp_path) == tmp_path / "slskd" / "slskd.yml"


def test_fresh_write_produces_the_full_template_at_mode_0600(tmp_path: Path):
    key = write_credentials(tmp_path, "digger", "not-a-real-password")
    path = config_path(tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600

    data = yaml.safe_load(path.read_text())
    assert data["remote_configuration"] is False
    assert data["web"]["port"] == 5030
    assert data["web"]["ip_address"] == "127.0.0.1"
    assert data["web"]["https"]["disabled"] is True
    assert data["web"]["authentication"]["username"] == "flackey"
    assert data["web"]["authentication"]["password"]  # generated, non-empty
    assert data["web"]["authentication"]["api_keys"]["flackey"]["key"] == key
    assert data["web"]["authentication"]["api_keys"]["flackey"]["cidr"] == "127.0.0.1/32"
    assert data["soulseek"]["username"] == "digger"
    assert data["soulseek"]["password"] == "not-a-real-password"
    assert data["soulseek"]["listen_port"] == 50300
    assert data["directories"]["downloads"] == str(tmp_path / "slskd" / "downloads")
    assert data["directories"]["incomplete"] == str(tmp_path / "slskd" / "incomplete")
    assert len(key) >= 16  # slskd's own minimum


def test_https_is_turned_off_even_on_a_config_written_before_the_fix(tmp_path: Path):
    """`web.ip_address` binds the HTTP listener only; slskd's HTTPS listener stayed on 0.0.0.0 and
    answered from the LAN on 5031. A config written by the older code has no `https` section at all, so
    this must be forced on every write rather than filled in only when missing."""
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"web": {"port": 5030, "ip_address": "127.0.0.1"},
                                    "soulseek": {"username": "old", "password": "old-fake"}}))
    write_credentials(tmp_path, "digger", "not-a-real-password")
    assert yaml.safe_load(path.read_text())["web"]["https"]["disabled"] is True


def test_second_write_preserves_generated_secrets_and_other_keys(tmp_path: Path):
    key1 = write_credentials(tmp_path, "digger", "not-a-real-password")
    path = config_path(tmp_path)
    before = yaml.safe_load(path.read_text())
    web_password_before = before["web"]["authentication"]["password"]

    key2 = write_credentials(tmp_path, "someone-else", "another-fake-password")
    assert key2 == key1
    assert path.stat().st_mode & 0o777 == 0o600  # the rewrite path stays 0600 too, not just the first write

    after = yaml.safe_load(path.read_text())
    assert after["web"]["authentication"]["password"] == web_password_before
    assert after["web"]["authentication"]["api_keys"]["flackey"]["key"] == key1
    assert after["web"]["port"] == 5030
    assert after["soulseek"]["listen_port"] == 50300
    assert after["directories"]["downloads"] == str(tmp_path / "slskd" / "downloads")
    assert after["directories"]["incomplete"] == str(tmp_path / "slskd" / "incomplete")
    assert after["soulseek"]["username"] == "someone-else"
    assert after["soulseek"]["password"] == "another-fake-password"


def test_write_fills_missing_secrets_on_a_hand_written_file(tmp_path: Path):
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("soulseek:\n  username: old\n")
    path.chmod(0o644)  # a hand-written file is not 0600; the rewrite must still end up 0600
    key = write_credentials(tmp_path, "digger", "not-a-real-password")
    assert path.stat().st_mode & 0o777 == 0o600
    data = yaml.safe_load(path.read_text())
    assert data["web"]["authentication"]["password"]
    assert data["web"]["authentication"]["api_keys"]["flackey"]["key"] == key


def test_write_fills_missing_secrets_when_web_section_is_a_bare_key(tmp_path: Path):
    # `web:` with nothing under it parses to `web: None` -- must still land on the 400/raise path
    # cleanly rather than crashing with an AttributeError deep inside dict.setdefault.
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("web:\nsoulseek:\n  username: old\n")
    key = write_credentials(tmp_path, "digger", "not-a-real-password")
    data = yaml.safe_load(path.read_text())
    assert data["web"]["authentication"]["api_keys"]["flackey"]["key"] == key


@pytest.mark.parametrize("content", ["- a\n- list\n", "just a string\n"])
def test_a_config_that_parses_to_a_non_mapping_raises(tmp_path: Path, content):
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(content)
    with pytest.raises(SlskdConfigError):
        write_credentials(tmp_path, "digger", "not-a-real-password")


def test_empty_username_raises_and_message_has_no_password(tmp_path: Path):
    with pytest.raises(SlskdConfigError) as ei:
        write_credentials(tmp_path, "", "not-a-real-password")
    assert "not-a-real-password" not in str(ei.value)


def test_whitespace_only_username_raises_and_message_has_no_username_or_password(tmp_path: Path):
    with pytest.raises(SlskdConfigError) as ei:
        write_credentials(tmp_path, "   ", "not-a-real-password")
    assert "   " not in str(ei.value)
    assert "not-a-real-password" not in str(ei.value)


def test_empty_password_raises_and_message_has_no_username(tmp_path: Path):
    with pytest.raises(SlskdConfigError) as ei:
        write_credentials(tmp_path, "a-real-looking-username", "")
    assert "a-real-looking-username" not in str(ei.value)


def test_read_api_key_returns_none_for_missing_and_malformed_config(tmp_path: Path):
    assert read_api_key(tmp_path) is None
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not: [valid, yaml, :::\n")
    assert read_api_key(tmp_path) is None
    path.write_text("- a\n- list\n")
    assert read_api_key(tmp_path) is None
    path.write_text("web:\n  authentication: {}\n")
    assert read_api_key(tmp_path) is None


def test_read_username_returns_none_then_the_username_after_a_write(tmp_path: Path):
    assert read_username(tmp_path) is None
    write_credentials(tmp_path, "digger", "not-a-real-password")
    assert read_username(tmp_path) == "digger"


def test_read_password_returns_none_for_missing_malformed_and_passwordless_configs(tmp_path: Path):
    """The one reader that hands a secret back still has to fail like the others: an absent, unparseable
    or half-written config is an ordinary state on the way to a first setup, not an error to raise from."""
    assert read_password(tmp_path) is None
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not: [valid, yaml, :::\n")
    assert read_password(tmp_path) is None
    path.write_text("- a\n- list\n")
    assert read_password(tmp_path) is None
    path.write_text("soulseek:\n  username: digger\n")
    assert read_password(tmp_path) is None
    path.write_text("soulseek:\n  username: digger\n  password: ''\n")
    assert read_password(tmp_path) is None


def test_read_password_returns_what_was_written(tmp_path: Path):
    """Soulseek has no password reset, so an owner who cannot read this string back has lost the account.
    That is the whole reason this reader exists."""
    write_credentials(tmp_path, "digger", "not-a-real-password")
    assert read_password(tmp_path) == "not-a-real-password"
