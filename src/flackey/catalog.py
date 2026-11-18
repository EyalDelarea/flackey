"""Catalog records: the dating rules shared by filing and the tag lookup.

A catalog record (`models.CatalogTrack`) is what a file is tagged from. Beatport used to supply them, scraped
from its search page before the download, until a Cloudflare challenge refused every lookup from 2026-10-14
and the scraper was removed. Records stored before then stay in the database and keep their tags; a new
track's record is built from its candidate and filled from Deezer and Discogs (`tag_lookup`, issue #146).
"""
from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterable

from .models import CatalogTrack
from .version import says_remaster

DURATION_SLACK_S = 3

#: A remaster names itself in its title, mix or release ("Remastered 2024", "2011 Remaster"). Its year is
#: the point of it, so nothing here dates one back to the original.
_DATE_RE = re.compile(r"(?:19|20)\d{2}(?:-\d{2}-\d{2})?")


is_remaster = says_remaster


def _date(value: str | None) -> str | None:
    """A YYYY or YYYY-MM-DD date, or None for anything else."""
    return value if value and _DATE_RE.fullmatch(value) else None


def earliest_date(record: CatalogTrack, dates: Iterable[str | None], asked: str | None = None) -> str | None:
    """The earliest of `dates` when its year is before the record's own, else None. A remaster keeps its
    own year, whether the record or the request (`asked`, its version text) says it is one: that release
    is what was asked for, and its year is what tells it from the original."""
    if is_remaster(record.title, record.mix_name, record.release_name, asked):
        return None
    own = _date(record.release_date)
    found = sorted(d for d in map(_date, dates) if d)
    if not found or (own and found[0][:4] >= own[:4]):
        return None
    return found[0]


def with_original_date(record: CatalogTrack, also: Iterable[str | None] = (), asked: str | None = None) -> CatalogTrack:
    """`record` with `original_date` set from the earliest `also` date another source vouched for (the
    Deezer record that was downloaded, Discogs' earliest year). A catalogue often sells a recording only on
    a later compilation or reissue, and its date is that release's: "One More Time" was tagged 2005. A
    remaster keeps its own year, and a date already found is kept unless an earlier one turns up. Nothing
    new is fetched for this."""
    earliest = earliest_date(record, [record.original_date, *also], asked)
    return dataclasses.replace(record, original_date=earliest) if earliest != record.original_date else record
