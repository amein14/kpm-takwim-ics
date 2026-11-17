"""Validate reviewed 2026 staging data against the official KPM source."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from scripts.data_integrity import events_sha256
from scripts.discover import ROOT
from scripts.extract import ExtractionError, build_staging_data

DEFAULT_STAGING = ROOT / "data" / "staging-events.json"
DEFAULT_MANIFEST = ROOT / "data" / "source-manifest.json"
DEFAULT_PDF_DIR = ROOT / "data" / "source-pdfs"
DEFAULT_STATE_GROUPS = ROOT / "config" / "state-groups.json"
DEFAULT_OUTPUT = ROOT / "data" / "events.json"
EXPECTED_EVENT_COUNTS = {
    "schooling_period": 26,
    "school_break": 8,
    "additional_kpm_holiday": 12,
    "festival_observance": 8,
}
EXPECTED_GROUP_STATES = {
    "A": ["Kedah", "Kelantan", "Terengganu"],
    "B": [
        "Johor",
        "Melaka",
        "Negeri Sembilan",
        "Pahang",
        "Perak",
        "Perlis",
        "Pulau Pinang",
        "Sabah",
        "Sarawak",
        "Selangor",
        "Wilayah Persekutuan Kuala Lumpur",
        "Wilayah Persekutuan Labuan",
        "Wilayah Persekutuan Putrajaya",
    ],
}


class ValidationError(RuntimeError):
    """Raised when staging data does not meet the publication checks."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Unable to read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{label} {path} must contain a JSON object.")
    return value


def _validate_state_groups(
    state_groups: dict[str, Any],
    academic_year: str,
    source_url: str,
    source_sha256: str,
) -> dict[str, list[str]]:
    if (
        state_groups.get("academic_year") != academic_year
        or state_groups.get("source_url") != source_url
        or state_groups.get("source_sha256") != source_sha256
    ):
        raise ValidationError("State-group mapping is not tied to the validated KPM source.")
    groups = state_groups.get("groups")
    if groups != EXPECTED_GROUP_STATES:
        raise ValidationError("2026 state-group mapping does not match the official KPM calendar.")
    if set(groups["A"]) & set(groups["B"]):
        raise ValidationError("A state cannot belong to both KPM groups.")
    return groups


def _validate_calendar_coverage(events: list[dict[str, Any]]) -> None:
    for group in ("A", "B"):
        intervals = sorted(
            (
                date.fromisoformat(event["start_date"]),
                date.fromisoformat(event["end_date"]),
            )
            for event in events
            if event["group"] == group
            and event["kind"] in {"schooling_period", "school_break"}
        )
        if not intervals or intervals[0][0] != date(2026, 1, 11 if group == "A" else 12):
            raise ValidationError(f"Group {group} school calendar has an unexpected first date.")
        if intervals[-1][1] != date(2026, 12, 31):
            raise ValidationError(f"Group {group} school calendar does not cover year-end break.")
        if any(
            previous_end + timedelta(days=1) != next_start
            for (_, previous_end), (next_start, _) in zip(intervals, intervals[1:])
        ):
            raise ValidationError(f"Group {group} school and break intervals have a gap or overlap.")


def _validate_event_records(
    events: Any,
    source: dict[str, Any],
    groups: dict[str, list[str]],
) -> list[dict[str, Any]]:
    if not isinstance(events, list) or not events:
        raise ValidationError("Staging data contains no events.")
    if Counter(event.get("kind") for event in events if isinstance(event, dict)) != Counter(
        EXPECTED_EVENT_COUNTS
    ):
        raise ValidationError("Event counts differ from the reviewed 2026 KPM calendar.")

    all_states = set(groups["A"]) | set(groups["B"])
    ids: set[str] = set()
    for event in events:
        if not isinstance(event, dict):
            raise ValidationError("Every event must be a JSON object.")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in ids:
            raise ValidationError("Event IDs must be non-empty and unique.")
        ids.add(event_id)
        if event.get("academic_year") != "2026":
            raise ValidationError(f"Event {event_id} is outside academic year 2026.")
        if event.get("group") not in {"A", "B", None}:
            raise ValidationError(f"Event {event_id} has an invalid group.")
        try:
            start = date.fromisoformat(event["start_date"])
            end = date.fromisoformat(event["end_date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError(f"Event {event_id} has an invalid date.") from exc
        if start.isoformat() != event["start_date"] or end.isoformat() != event["end_date"]:
            raise ValidationError(f"Event {event_id} dates must use ISO YYYY-MM-DD format.")
        if start.year != 2026 or end.year != 2026 or end < start:
            raise ValidationError(f"Event {event_id} has an invalid date range.")
        source_ref = event.get("source")
        if (
            not isinstance(source_ref, dict)
            or source_ref.get("filename") != source["filename"]
            or source_ref.get("source_url") != source["source_url"]
            or source_ref.get("sha256") != source["sha256"]
            or source_ref.get("page_number") not in {1, 2, 3}
            or not source_ref.get("text")
        ):
            raise ValidationError(f"Event {event_id} is missing matching source provenance.")
        included = set(event.get("states_included", []))
        excluded = set(event.get("states_excluded", []))
        if not included.issubset(all_states) or not excluded.issubset(all_states):
            raise ValidationError(f"Event {event_id} refers to an unknown state.")
        if included & excluded:
            raise ValidationError(f"Event {event_id} both includes and excludes a state.")
        event_states = set(groups[event["group"]]) if event["group"] else all_states
        if included and not included.intersection(event_states):
            raise ValidationError(f"Event {event_id} state scope conflicts with its KPM group.")
        if excluded and not event_states.difference(excluded):
            raise ValidationError(f"Event {event_id} excludes every state in its KPM group.")

    _validate_calendar_coverage(events)
    return events


def validate_staging(
    staging: dict[str, Any],
    fresh_staging: dict[str, Any],
    state_groups: dict[str, Any],
) -> dict[str, Any]:
    if staging.get("schema_version") != 1 or staging.get("status") != "staging_only_unvalidated":
        raise ValidationError("Input must be Phase 2 staging data with schema version 1.")
    if staging.get("events") != fresh_staging.get("events"):
        raise ValidationError("Staging event data differs from a fresh extraction of the source PDF.")
    source_record = next(
        (
            item
            for item in fresh_staging.get("documents", [])
            if item.get("academic_year") == "2026"
        ),
        None,
    )
    if not isinstance(source_record, dict) or source_record.get("extraction_status") != "extracted_unvalidated":
        raise ValidationError("The 2026 source document was not successfully extracted.")
    if urlparse(source_record.get("source_url", "")).hostname != "www.moe.gov.my":
        raise ValidationError("The 2026 source is not hosted on the official KPM website.")
    groups = _validate_state_groups(
        state_groups,
        "2026",
        source_record["source_url"],
        source_record["sha256"],
    )
    events = _validate_event_records(staging.get("events"), source_record, groups)
    if len(events) != sum(EXPECTED_EVENT_COUNTS.values()):
        raise ValidationError("The number of reviewed events is inconsistent.")
    pending_2027 = next(
        (
            item
            for item in fresh_staging.get("documents", [])
            if item.get("academic_year") == "2027"
        ),
        None,
    )
    if pending_2027 is not None and pending_2027.get("extraction_status") != "pending_ocr":
        raise ValidationError("The image-only 2027 document must remain pending OCR.")

    return {
        "schema_version": 1,
        "academic_year": "2026",
        "status": "validated",
        "validation": {
            "status": "verified",
            "method": (
                "Dates and scopes checked against the official KPM PDF pages 1–3; "
                "source checksum, parser reproducibility, group coverage, and event counts verified."
            ),
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "source_filename": source_record["filename"],
            "source_url": source_record["source_url"],
            "source_sha256": source_record["sha256"],
            "group_states": groups,
            "reviewed_pages": [1, 2, 3],
            "event_count": len(events),
            "events_sha256": events_sha256(events),
        },
        "events": events,
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
            temporary_path = Path(file.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", type=Path, default=DEFAULT_STAGING)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--state-groups", type=Path, default=DEFAULT_STATE_GROUPS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Verify existing approved data without writing it.",
    )
    args = parser.parse_args(argv)
    try:
        staging = _read_json(args.staging, "staging data")
        state_groups = _read_json(args.state_groups, "state-group mapping")
        fresh_staging = build_staging_data(args.manifest, args.pdf_dir)
        approved = validate_staging(staging, fresh_staging, state_groups)
        if args.check_only:
            existing = _read_json(args.output, "approved event data")
            if (
                existing.get("schema_version") != approved["schema_version"]
                or existing.get("academic_year") != approved["academic_year"]
                or existing.get("status") != "validated"
                or existing.get("events") != approved["events"]
            ):
                raise ValidationError("Approved events are stale or do not match the validated source.")
            approved_metadata = approved["validation"]
            existing_metadata = existing.get("validation")
            if not isinstance(existing_metadata, dict) or any(
                existing_metadata.get(field) != approved_metadata[field]
                for field in (
                    "status",
                    "source_filename",
                    "source_url",
                    "source_sha256",
                    "group_states",
                    "reviewed_pages",
                    "event_count",
                    "events_sha256",
                )
            ):
                raise ValidationError("Approved validation metadata differs from the current KPM document.")
        else:
            _write_json(args.output, approved)
    except (ExtractionError, ValidationError, OSError) as exc:
        print(f"Validation failed: {exc}", file=sys.stderr)
        return 1
    action = "Verified approved" if args.check_only else "Validated"
    print(f"{action} {approved['validation']['event_count']} event(s) from the 2026 KPM calendar.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
