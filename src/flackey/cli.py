from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer

from .config import Settings, load_settings
from .logsetup import configure_logging

app = typer.Typer(name="flackey", help="Flackey: paste Deezer links, get verified, tagged tracks for Rekordbox",
                  no_args_is_help=True)
_state: dict = {}


def _settings() -> Settings:
    return load_settings(_state.get("env"))


@app.callback()
def main(env: Path | None = typer.Option(None, "--env", help="Path to .env (default ./.env)"),  # noqa: B008
         verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    _state["env"] = env
    configure_logging(_settings().data_dir, verbose=verbose)


@app.command()
def start(no_browser: bool = typer.Option(False, "--no-browser", help="Serve only; open http://localhost:8765 yourself"),
          browser: bool = typer.Option(False, "--browser", help="Open the system browser instead of the app window")) -> None:
    """Run the worker and open the Flackey window; stops when the window closes or on Ctrl-C."""
    from .app import run

    try:
        if no_browser or browser:
            asyncio.run(run(_settings(), open_browser=browser))
        else:
            from .desktop import run_in_window

            run_in_window(_settings())
    except KeyboardInterrupt:
        pass
    typer.echo("stopped")


@app.command()
def status() -> None:
    """Print queue and library counts."""
    from .store import Store

    s = _settings()
    st = Store(s.db_path).stats()
    for state, n in sorted(st["requests_by_state"].items()):
        typer.echo(f"{state}: {n}")
    typer.echo(f"tracks: {st['tracks']}")
    typer.echo(f"rejections: {st['rejections']}")
    typer.echo(f"library: {st['bytes'] / 1e6:.1f} MB at {s.library_root}")
    readings = st["interpretations"]
    total = sum(n for k, n in readings.items() if k != "confirmed_by_other")
    if total:
        # The shadow comparison of noisy titles' readings: `ambiguous` is the share that would have needed
        # the owner to say which reading they meant, out of the ones the comparison could judge.
        typer.echo(f"noisy titles: {total} (readings compared in the background; nothing was changed)")
        for klass in ("confirmed", "agreed", "ambiguous", "skipped", "pending"):
            n = readings.get(klass, 0)
            other = readings["confirmed_by_other"]
            extra = f" ({other} by a reading other than the one searched)" if klass == "confirmed" and other else ""
            typer.echo(f"  {klass}: {n}{extra}")
        judged = sum(readings.get(k, 0) for k in ("confirmed", "agreed", "ambiguous"))
        if judged:
            ambiguous = readings.get("ambiguous", 0)
            typer.echo(f"  would ask: {ambiguous} of {judged} ({ambiguous / judged:.0%})")


@app.command()
def export() -> None:
    """Rewrite the M3U8 playlist files (one per YouTube playlist) for Rekordbox import."""
    from .export import write_playlists
    from .store import Store

    s = _settings()
    s.library_root.mkdir(parents=True, exist_ok=True)
    for p in write_playlists(Store(s.db_path), s.library_root):
        typer.echo(str(p))


@app.command()
def add(text: str) -> None:
    """Enqueue a YouTube or YouTube Music link, exactly like the paste bar in the UI."""
    from .inbox import BadLink, Inbox
    from .store import Store

    s = _settings()
    try:
        typer.echo(asyncio.run(Inbox(Store(s.db_path)).submit(text)).summary())
    except BadLink as e:
        typer.echo(str(e))


@app.command()
def login() -> None:
    """Log the owner's Telegram account in (one-time, interactive)."""
    from telethon import TelegramClient

    s = _settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)

    async def _login() -> None:
        client = TelegramClient(str(s.session_path), s.telegram_api_id, s.telegram_api_hash)
        await client.start()  # prompts for phone, code, and 2FA password in the terminal
        me = await client.get_me()
        typer.echo(f"logged in as {me.first_name} (@{me.username}) id={me.id}")
        await client.disconnect()

    asyncio.run(_login())
    for p in s.data_dir.glob("owner.session*"):
        os.chmod(p, 0o600)


@app.command()
def sweep(out: Path = typer.Option(Path("sweep.md"), "--out",  # noqa: B008
                                   help="Where to write the markdown table")) -> None:
    """Fingerprint every filed track and its Deezer record against the YouTube source; write a report.
    Reads the library, YouTube and Deezer previews; changes nothing in the library."""
    import httpx

    from .store import Store
    from .sweep import render, run

    s = _settings()

    async def go() -> list:
        async with httpx.AsyncClient() as http:
            return await run(Store(s.db_path), s, http, progress=typer.echo)

    rows = asyncio.run(go())
    out.write_text(render(rows, s.lossless_fingerprint_min), encoding="utf-8")
    typer.echo(f"wrote {out}")


@app.command("verify-folder")
def verify_folder(folder: Path, out: Path = typer.Option(Path("verify-folder.jsonl"), "--out",  # noqa: B008
                                                      help="One JSON line per file; it names every file"),
                  jobs: int = typer.Option(2, "--jobs", help="Files checked at once")) -> None:
    """Run the old and the new quality check over every audio file in a folder, read-only; print how they
    compare and every file they disagree on (issue #97)."""
    from .verify_report import run, table

    typer.echo(table(run(folder, out, jobs)))


@app.command("replay-picks")
def replay_picks(out: Path = typer.Option(Path("replay.md"), "--out",  # noqa: B008
                                          help="Where to write the markdown table")) -> None:
    """Re-run every stored no-pick report through the proposed hard rules and rankers; no network.
    Reads the attempt table only and changes nothing."""
    from .lossless import HARD_RULES, IDENTITY_RANKERS, PEER_RANKERS
    from .replay import render, run
    from .store import Store

    rows = run(Store(_settings().db_path), rules=HARD_RULES, rankers=IDENTITY_RANKERS + PEER_RANKERS)
    out.write_text(render(rows), encoding="utf-8")
    typer.echo(f"{len(rows)} attempts replayed; wrote {out}")


@app.command("tags-dry-run")
def tags_dry_run(out: Path = typer.Option(Path("tags-dry-run.md"), "--out",  # noqa: B008
                                          help="Where to write the markdown table"),
                 limit: int = typer.Option(500, "--limit", help="At most this many tracks, newest first")) -> None:
    """Look up every Beatport-tagged track in Deezer and Discogs as if Beatport were down, and compare the
    tags each would get with Beatport's. Changes nothing in the library; Discogs answers are cached."""
    import httpx

    from .store import Store
    from .tag_dryrun import render, run
    from .tag_lookup import default_lookup

    s = _settings()

    async def go() -> list:
        async with httpx.AsyncClient(timeout=20) as http:
            return await run(Store(s.db_path), default_lookup(http, s.data_dir), limit=limit,
                             progress=typer.echo)

    rows = asyncio.run(go())
    out.write_text(render(rows), encoding="utf-8")
    typer.echo(f"{len(rows)} tracks looked up; wrote {out}")


lossless_app = typer.Typer(help="Lossless-upgrade tools (Soulseek through slskd)", no_args_is_help=True)
app.add_typer(lossless_app, name="lossless")


@lossless_app.command()
def replay(request_id: int) -> None:
    """Re-run the pick on the stored search responses of a request under the current policy and diff it."""
    import json
    from collections import Counter
    from pathlib import Path

    from .lossless import PickReport, pick, policy_from_settings
    from .source.slskd import parse_response
    from .store import Store

    s = _settings()
    attempt = Store(s.db_path).get_attempt_for_request(request_id)
    if attempt is None or not attempt.report:
        typer.echo(f"no attempt with a pick report for request {request_id}")
        raise typer.Exit(1)
    raw = next(iter(sorted(Path(attempt.raw_dir or "").glob("*-responses.json"))), None)
    if raw is None:
        typer.echo(f"no responses.json under {attempt.raw_dir} (pruned after {s.lossless_keep_raw_days} days)")
        raise typer.Exit(1)
    # The stored report and the raw responses file both hold peer-supplied and possibly partially-written
    # data (spec: nothing from a peer is trusted); a malformed or truncated file is an error message, not
    # a traceback.
    try:
        stored = PickReport.from_dict(attempt.report)
        files = [f for r in json.loads(raw.read_text(encoding="utf-8")) for f in parse_response(r)]
    except (json.JSONDecodeError, KeyError, IndexError, AttributeError, TypeError, ValueError,
            UnicodeDecodeError, OSError) as e:
        typer.echo(f"stored attempt {attempt.id} is corrupt: {e}")
        raise typer.Exit(1) from None
    now = pick(files, stored.reference, policy_from_settings(s))

    def who(report: PickReport) -> str:
        return f"{report.chosen.username} {report.chosen.name}" if report.chosen else "-"

    typer.echo(f"attempt {attempt.id} ({attempt.outcome}) · {len(files)} files · query {attempt.query!r}")
    typer.echo(f"stored: {who(stored)}")
    typer.echo(f"now:    {who(now)}")
    if now.chosen is None:
        typer.echo("no pick now")
    elif stored.chosen == now.chosen:
        typer.echo("same pick")
    else:
        typer.echo("different pick")
    before, after = Counter(r.rule for r in stored.rejections), Counter(r.rule for r in now.rejections)
    for rule in sorted(set(before) | set(after)):
        mark = "" if before[rule] == after[rule] else "  <-"
        typer.echo(f"  {rule:15} stored {before[rule]:3}  now {after[rule]:3}{mark}")
