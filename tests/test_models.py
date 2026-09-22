import re
from pathlib import Path

import pytest

from flackey.models import (
    FAILED_STATES,
    RETRYABLE_STATES,
    TERMINAL_STATES,
    CatalogTrack,
    Query,
    RequestState,
    norm,
)


def test_query_search_text_prefers_structured_fields():
    q = Query(raw="whatever", artist="Astral Projection", title="Into the Void")
    assert q.search_text() == "Astral Projection Into the Void"
    q.version = "Vini Vici Remix"
    assert q.search_text() == "Astral Projection Into the Void Vini Vici Remix"
    assert Query(raw="free text").search_text() == "free text"


def test_catalog_track_derived_fields():
    t = CatalogTrack(id=1, artist="a", title="t", mix_name="Original Mix", label="l",
                     genre="g", release_date="2022-06-03", duration_ms=442816)
    assert t.duration_s == 443
    assert t.year == "2022"


def _ct(mix: str) -> CatalogTrack:
    return CatalogTrack(id=1, artist="Astral Projection", title="Into the Void", mix_name=mix,
                        label="Trust in Trance", genre="Psy-Trance")


@pytest.mark.parametrize("mix,expected", [
    ("Aboriginal Mix", "Into the Void (Aboriginal Mix)"),
    ("Original Mix", "Into the Void"),
    ("Original", "Into the Void"),
    ("Original Version", "Into the Void"),
])
def test_display_title_word_boundary_original(mix: str, expected: str):
    assert _ct(mix).display_title == expected


def test_query_search_text_drops_original_mix():
    # Deezer titles never carry "Original Mix"; the source bot answers "No results" when it is appended
    q = Query(raw="", artist="Goasia", title="Love & Peace", version="Original Mix")
    assert q.search_text() == "Goasia Love & Peace"


def test_norm_joins_dotted_acronyms_and_strips_punctuation():
    assert norm("L.S.D.") == "lsd"
    assert norm("Love & Peace") == "love peace"
    assert norm("S.U.N. Project") == "sun project"


def test_failed_states_are_terminal_and_only_some_of_them_come_back():
    """"Terminal" here means the pipeline has let go of the request, not that the state can never change:
    two of the four failures are handed straight back to the queue by `Worker.retry`. Conflating the two
    is what let the Failed badge count 45 while the button under it offered to retry 12."""
    assert FAILED_STATES < TERMINAL_STATES
    assert RETRYABLE_STATES < FAILED_STATES
    assert TERMINAL_STATES - FAILED_STATES == {RequestState.DONE, RequestState.DUPLICATE}


def _ts_map(name: str) -> dict[str, str]:
    """One `const NAME: Record<...> = { key: 'value', ... }` table out of presentation.ts, as a dict."""
    ts = (Path(__file__).resolve().parents[1] / "web" / "src" / "presentation.ts").read_text()
    block = re.search(rf"const {name}: Record<[^>]+> = \{{(.*?)\n\}}", ts, re.DOTALL)
    assert block, f"presentation.ts no longer has a {name} table"
    return dict(re.findall(r"(\w+): '(\w+)'", block.group(1)))


def test_failed_states_match_the_ui():
    """The browser decides which rows go in the Failed tab and which of them get a Try again button, and it
    has to decide it the same way the worker does. Four separate lists of "the failed states" and a fifth of
    "the retryable ones" had already drifted once; pin them here rather than plumb the sets through the API
    (same trade as `test_miss_reasons_match_the_ui`)."""
    buckets, finality = _ts_map("BUCKET_OF"), _ts_map("FINALITY_OF")
    every = {s.value for s in RequestState}
    assert set(buckets) == every, "BUCKET_OF must classify every state"
    assert set(finality) == every, "FINALITY_OF must classify every state"
    assert {s for s, b in buckets.items() if b == "failed"} == {s.value for s in FAILED_STATES}
    assert {s for s, f in finality.items() if f == "retryable"} == {s.value for s in RETRYABLE_STATES}
    # Nothing the pipeline still holds may be advertised as retryable or as finished.
    assert {s for s, f in finality.items() if f == "open"} == every - {s.value for s in TERMINAL_STATES}


def test_stage_names_match_the_ui():
    """`failed_stage` is a free-text column: the worker writes a word and the browser looks it up. That is
    two copies of one vocabulary either side of an HTTP boundary, with nothing but this test between them --
    rename a stage on one side and the Failed tab silently files those rows under Unknown, which is exactly
    the drift `test_failed_states_match_the_ui` above exists to catch for the states.

    Only the intersection is assertable, and deliberately so. The worker's table answers "which rung was the
    row on when it turned to error", so it holds the six live states and nothing else; the browser's also
    reads a stage off the four failure states by name, which the worker never writes. And `_ts_map` cannot
    see `done`/`duplicate` at all -- their value is a bare `null`, not a quoted word -- so this must not
    assert on the table's length or on equality of the key sets."""
    from flackey.worker import STAGE_OF_STATE

    ts = _ts_map("STAGE_OF")
    assert ts, "presentation.ts no longer has a STAGE_OF table"
    # Total over the live states, checked before the values are. Iterating the table alone would let an
    # entry be dropped and the test go quiet about that state rather than fail -- `STAGE_OF_STATE.get(was,
    # "unknown")` then files those failures under Unknown and nothing says so. Python has no equivalent of
    # the TypeScript totality check the other table gets from its `Record`, so this is that check.
    assert set(STAGE_OF_STATE) == set(RequestState) - TERMINAL_STATES, (
        "STAGE_OF_STATE must name every state a request can still be failing out of"
    )
    for state, stage in STAGE_OF_STATE.items():
        assert ts.get(state.value) == stage, (
            f"worker calls {state.value} '{stage}'; presentation.ts calls it {ts.get(state.value)!r}"
        )
    # Every word either side writes has to be one the browser's Stage union admits, or the chip it belongs
    # to is never drawn. `unknown` is in the union but in neither table's values: it is the fallback both
    # sides reach for, so it is spelled out here rather than derived.
    union = set(re.findall(r"'(\w+)'", re.search(r"export type Stage = ([^\n]+)", (
        Path(__file__).resolve().parents[1] / "web" / "src" / "presentation.ts").read_text()).group(1)))
    assert set(STAGE_OF_STATE.values()) <= union
    assert "unknown" in union
