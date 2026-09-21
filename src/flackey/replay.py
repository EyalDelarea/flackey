"""Re-run the stored no-pick reports through a different gate, without the network (issue #69's
measurement). `PickReport.to_dict` keeps every rejected file, so the question "what would rank first now?"
is answerable from the attempt table alone -- no search, no peer, nothing downloaded."""
from __future__ import annotations

from dataclasses import dataclass

from .lossless import LosslessFile, PickPolicy, Ranker, Reference, Rule, pick
from .store import Store

TOP = 4                    # how many survivors a row carries: the pick and the three it would fall back to


@dataclass(frozen=True)
class ReplayRow:
    attempt_id: int
    request_id: int
    text: str
    asked_s: int | None
    seen: int
    survivors: int
    top: list[tuple[str, int | None, int | None]]     # (file name, length, delta to the asked length)


def run(store: Store, *, rules: list[tuple[str, Rule]], rankers: list[tuple[str, Ranker]],
        limit: int = 10_000) -> list[ReplayRow]:
    rows: list[ReplayRow] = []
    for a in store.list_attempts(limit=limit, outcome="no_pick"):
        if not a.report:
            continue
        rep = a.report
        files = [LosslessFile(**r["file"]) for r in rep["rejections"]] + [LosslessFile(**f) for f in rep["survivors"]]
        ref = Reference(**rep["reference"])
        report = pick(files, ref, PickPolicy.from_dict(rep["policy"]), rules=rules, rankers=rankers)
        try:
            req = store.get_request(a.request_id)
            text, asked = req.raw_text, req.query_duration_s
        except KeyError:                    # the request was deleted; the attempt still says what was asked
            text, asked = a.query, ref.requested_duration_s
        top = [(f.name, f.length_s, None if asked is None or f.length_s is None else abs(f.length_s - asked))
               for f in report.survivors[:TOP]]
        rows.append(ReplayRow(a.id, a.request_id, text, asked, len(files), len(report.survivors), top))
    return rows


def render(rows: list[ReplayRow]) -> str:
    with_pick = [r for r in rows if r.survivors]
    near = [r for r in with_pick if r.top[0][2] is not None and r.top[0][2] <= 10]
    far = [r for r in with_pick if r.top[0][2] is not None and r.top[0][2] > 60]
    lines = [f"# No-pick replay: {len(rows)} attempts", "",
             f"- attempts with a survivor now: {len(with_pick)}",
             f"- top pick within 10 s of the asked length: {len(near)}",
             f"- top pick more than 60 s off: {len(far)}", "",
             "| attempt | request | asked s | seen | survivors | #1 file | #1 s | delta | #2 | #3 | #4 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        cells = [str(r.attempt_id), r.text[:50].replace("|", "/"), str(r.asked_s or "?"), str(r.seen), str(r.survivors)]
        first = r.top[0] if r.top else ("-", None, None)
        cells += [first[0][:60].replace("|", "/"), str(first[1] or "?"), str(first[2] if first[2] is not None else "?")]
        cells += [f"{n[:30]} ({s}s)" for n, s, _ in r.top[1:4]] + ["-"] * (3 - len(r.top[1:4]))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"
