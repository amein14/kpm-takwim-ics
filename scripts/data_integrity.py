"""Helpers for deterministic integrity checks on calendar data."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def events_sha256(events: list[dict[str, Any]]) -> str:
    serialized = json.dumps(
        events,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()
