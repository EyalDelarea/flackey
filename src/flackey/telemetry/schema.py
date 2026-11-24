"""The one schema every event is built through (spec §6.3).

`assets/telemetry_schema_v1.json` is a byte-for-byte copy of the collector's `schemas/v1.json`
(EyalDelarea/flackey-telemetry); `test_telemetry.test_schema_is_the_collectors` pins its SHA-256 to the
collector's `v1.json.sha256`, so the two cannot drift without a test failing. The checks below are the
collector's own, so an event this module lets through is one the collector accepts.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from functools import cache
from pathlib import Path

SCHEMA_VERSION = 1
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "assets" / f"telemetry_schema_v{SCHEMA_VERSION}.json"
MAX_STRING = 50
# Used with `fullmatch`, so the whole value must match.
_UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class Field:
    def __init__(self, spec: dict, buckets: dict[str, list[str]], events: set[str]):
        self.kind = spec.get("type")
        self.values: frozenset[str] | None = None
        if "enum" in spec:
            self.kind, self.values = "enum", frozenset(spec["enum"])
        elif "bucket" in spec:
            self.kind, self.values = "enum", frozenset(buckets[spec["bucket"]])
        elif self.kind == "enum_array":
            self.values = frozenset(spec["values"])
        elif self.kind == "event":
            self.kind, self.values = "enum", frozenset(events)
        self.max = spec.get("max")
        self.pattern = re.compile(spec["pattern"]) if self.kind == "regex" else None

    def ok(self, value: object) -> bool:
        """The collector's rule: never coerced, so "false" is not a bool and True is not an int."""
        k = self.kind
        if k == "enum":
            return isinstance(value, str) and value in self.values
        if k == "bool":
            return isinstance(value, bool)
        if k == "int":
            return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= self.max
        if k == "uuid4":
            return isinstance(value, str) and bool(_UUID4.fullmatch(value))
        if k == "date":
            if not isinstance(value, str) or not _DATE.fullmatch(value):
                return False
            try:
                dt.date.fromisoformat(value)
            except ValueError:
                return False
            return True
        if k == "regex":
            return isinstance(value, str) and len(value) <= MAX_STRING and bool(self.pattern.fullmatch(value))
        if k == "enum_array":
            return (isinstance(value, list) and len(value) <= len(self.values) and len(set(value)) == len(value)
                    and all(isinstance(v, str) and v in self.values for v in value))
        return False


class Schema:
    def __init__(self, raw: dict):
        self.raw = raw
        self.version: int = raw["schema"]
        buckets = raw["buckets"]
        names = set(raw["events"])
        self.common = {k: Field(v, buckets, names) for k, v in raw["common"].items()}
        self.events = {e: {k: Field(v, buckets, names) for k, v in f.items()} for e, f in raw["events"].items()}

    def check_common(self, envelope: dict) -> str | None:
        """The first envelope field that would make the collector refuse the batch, or None."""
        for key, f in self.common.items():
            if not f.ok(envelope.get(key)):
                return key
        return None

    def clean(self, event: str, fields: dict) -> tuple[dict, list[str]]:
        """The fields this event may carry, and the names of those dropped. A None value is simply not
        sent; anything else that fails its type is dropped and counted, never sent (spec §6.1)."""
        spec = self.events.get(event)
        if spec is None:
            raise KeyError(event)
        kept, dropped = {}, []
        for key, value in fields.items():
            if value is None:
                continue
            f = spec.get(key)
            if f is None or not f.ok(value):
                dropped.append(key)
            else:
                kept[key] = value
        return kept, dropped


@cache
def schema() -> Schema:
    return Schema(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
