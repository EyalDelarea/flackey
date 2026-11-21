"""Why the library trusts a filed file (the receipt), and checking filed files again with today's check.

The evidence is written when a track is filed (`worker.filing`); this module only reads it back in words,
and adds one row of its own -- `RECHECK` -- when the owner asks for a file to be checked again. A re-check
never moves, re-tags, replaces or deletes anything: it is a second opinion written beside the first."""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from .models import Track, edge_text
from .store import Store
from .verify import VerifyError, summary, verify

log = logging.getLogger(__name__)

RECHECK = "recheck"


def _check(label: str, value: str, ok: bool | None, note: str | None = None) -> dict:
    """One line of the receipt. `ok` True is a pass, False a failure the owner should know about, None a
    plain fact or a check that was not run."""
    return {"label": label, "value": value, "ok": ok, "note": note}


def _recording(ev: dict | None) -> dict:
    if ev is None:
        return _check("Same recording", "not checked", None, "Filed before this check existed, or with nothing to compare against.")
    status, score = ev.get("status"), ev.get("score")
    if status == "matched" and score is not None:
        return _check("Same recording", f"{score * 100:.1f}% match", True, ev.get("reference"))
    if status == "failed":
        value = f"{score * 100:.1f}% match" if score is not None else "did not match"
        return _check("Same recording", value, False, ev.get("reason"))
    return _check("Same recording", "not checked", None, ev.get("reason"))


def _quality(evidence: dict[str, dict], t: Track) -> list[dict]:
    kept = evidence.get("quality_check")
    if kept is not None and kept.get("status") == "failed":
        return [_check("Quality check", "kept anyway", False,
                       f"You kept this file after the check refused it: {kept.get('reason') or 'no reason recorded'}.")]
    ver = evidence.get("verification")
    if ver is None:
        return [_check("Quality check", "passed (older check)", True if t.verified_at else None,
                       "Filed before the detailed check was recorded. Check it again to see today's answer.")]
    tier = ver.get("tier")
    if tier == "unverified":
        return [_check("Quality check", "unverified", None, ver.get("reason"))]
    if tier == "clear":
        return [_check("Quality check", "passed", True, ver.get("reason"))]
    return [_check("Quality check", str(tier or "recorded"), None, ver.get("reason"))]


def receipt(store: Store, t: Track) -> dict:
    """What the page shows for one library track: where it came from and every check it went through."""
    evidence: dict[str, dict] = {}
    for e in store.list_evidence(t.id):
        evidence[e.kind] = e.value      # oldest first, so the latest row of a kind wins
    asked = None
    if t.request_id is not None:
        try:
            asked = store.get_request(t.request_id).raw_text or None
        except KeyError:
            pass
    checks = [_recording(evidence.get("recording_match")), *_quality(evidence, t)]
    version = evidence.get("version_check")
    if version is not None:
        confirmed = version.get("status") == "confirmed"
        checks.append(_check("Version", "confirmed" if confirmed else "unconfirmed", True if confirmed else None,
                             version.get("reason")))
    if t.cutoff_hz:
        # A fact, not a pass: "no edge found" is also what silence gives (issue #97)
        checks.append(_check("Spectrum", edge_text(t.cutoff_hz), None))
    kept_anyway = (evidence.get("quality_check") or {}).get("status") == "failed"
    return {"track_id": t.id, "asked_for": asked, "filed_at": t.added_at,
            "replaced_at": t.verified_at if t.verified_at and t.verified_at > t.added_at else None,
            "lossless_source": t.source_fmt is not None, "kept_anyway": kept_anyway,
            "unverified": (evidence.get("verification") or {}).get("tier") == "unverified" and not kept_anyway,
            "checks": checks, "has_spectrogram": spectrogram_of(store, t) is not None,
            "recheck": evidence.get(RECHECK)}


def spectrogram_of(store: Store, t: Track) -> Path | None:
    """The picture of this file's spectrum: the one taken at filing, else the one a re-check took."""
    if t.spectrogram_path is not None:
        return t.spectrogram_path
    shot = (store.evidence_of(t.id, RECHECK) or {}).get("spectrogram")
    return Path(shot) if shot else None


def measure(t: Track, spectrogram_dir: Path, rule: str = "tiers") -> dict:
    """Today's quality check on a filed file, as the value a `RECHECK` row holds. Blocking -- it decodes
    the whole file -- so it is run off the event loop. Reads the file only: the library, the file and the
    row's own verdict are left exactly as they are."""
    now = datetime.now(UTC).isoformat(timespec="seconds")
    if not t.path.is_file():
        return {"checked_at": now, "status": "missing", "reason": "the file is not there"}
    # A picture only where the track has none, named after the track so a second re-check replaces it
    shoot = spectrogram_dir if t.spectrogram_path is None else None
    try:
        if shoot is not None:
            shoot.mkdir(parents=True, exist_ok=True)
        v = verify(t.path, shoot, f"track{t.id}-recheck", rule)
    except (VerifyError, OSError) as e:
        return {"checked_at": now, "status": "error", "reason": str(e)[:300]}
    return {"checked_at": now, "status": "checked", "tier": v.level, "passed": v.passed,
            "reason": v.reason, "cutoff_hz": v.cutoff_hz, "summary": summary(v.analysis),
            **({"spectrogram": str(v.spectrogram_path)} if v.spectrogram_path else {})}


def record(store: Store, t: Track, value: dict) -> None:
    try:
        store.get_track(t.id)
    except KeyError:      # forgotten while it was being checked: an evidence row would belong to nothing
        return
    store.set_evidence(t.id, RECHECK, value)
    log.info("re-checked track#%d %s: %s", t.id, t.path.name, value.get("tier") or value["status"])


def flagged(value: dict | None) -> bool:
    """A re-check the owner should look at: anything but a clean pass."""
    return value is not None and not (value.get("status") == "checked" and value.get("tier") == "clear")
