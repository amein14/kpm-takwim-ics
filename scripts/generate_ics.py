"""Generate Group A and Group B calendars from validated KPM events."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from icalendar import Calendar, Event

from scripts.data_integrity import events_sha256
from scripts.discover import ROOT

DEFAULT_EVENTS = ROOT / "data" / "events.json"
DEFAULT_STATE_GROUPS = ROOT / "config" / "state-groups.json"
DEFAULT_OUTPUT_DIR = ROOT / "site"
DEFAULT_HOLIDAY_LIST = ROOT / "site" / "school-holidays.json"
CALENDAR_DOMAIN = "amein14.github.io"
HOLIDAY_KINDS = {"school_break", "additional_kpm_holiday", "festival_observance"}


class FeedError(RuntimeError):
    """Raised when validated event data cannot produce a sound calendar."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FeedError(f"Unable to read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise FeedError(f"{label} {path} must contain a JSON object.")
    return value


def _validate_inputs(
    approved: dict[str, Any],
    state_groups: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    if approved.get("schema_version") != 1 or approved.get("status") != "validated":
        raise FeedError("Refusing to publish events that are not marked validated.")
    academic_year = approved.get("academic_year")
    validation = approved.get("validation")
    if academic_year != "2026" or not isinstance(validation, dict) or validation.get("status") != "verified":
        raise FeedError("Approved event data has no verified 2026 validation record.")
    source_url = validation.get("source_url")
    source_sha256 = validation.get("source_sha256")
    if (
        not isinstance(source_url, str)
        or not source_url.startswith("https://www.moe.gov.my/")
        or not isinstance(source_sha256, str)
        or len(source_sha256) != 64
    ):
        raise FeedError("Approved event data has invalid official-source metadata.")
    if (
        state_groups.get("academic_year") != academic_year
        or state_groups.get("source_url") != source_url
        or state_groups.get("source_sha256") != source_sha256
        or state_groups.get("groups") != validation.get("group_states")
    ):
        raise FeedError("State-group mapping does not match the approved source document.")
    groups = state_groups.get("groups")
    if not isinstance(groups, dict) or not all(
        isinstance(groups.get(group), list) and groups[group] for group in ("A", "B")
    ):
        raise FeedError("State-group mapping must define non-empty Group A and Group B lists.")
    if set(groups["A"]) & set(groups["B"]):
        raise FeedError("A state cannot belong to both KPM groups.")
    events = approved.get("events")
    if not isinstance(events, list) or not events:
        raise FeedError("Approved event data contains no events.")
    if (
        validation.get("event_count") != len(events)
        or validation.get("events_sha256") != events_sha256(events)
    ):
        raise FeedError("Approved events do not match their validation integrity record.")
    seen_ids: set[str] = set()
    all_states = set(groups["A"]) | set(groups["B"])
    for event in events:
        if not isinstance(event, dict):
            raise FeedError("Every approved event must be a JSON object.")
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or not event_id or event_id in seen_ids:
            raise FeedError("Event IDs must be non-empty and unique.")
        seen_ids.add(event_id)
        group = event.get("group")
        if event.get("academic_year") != academic_year or group not in {"A", "B", None}:
            raise FeedError(f"Event {event_id} has an invalid academic year or group.")
        try:
            start = date.fromisoformat(event["start_date"])
            end = date.fromisoformat(event["end_date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FeedError(f"Event {event_id} has invalid ISO dates.") from exc
        if (
            start.isoformat() != event["start_date"]
            or end.isoformat() != event["end_date"]
            or start.year != 2026
            or end.year != 2026
            or end < start
        ):
            raise FeedError(f"Event {event_id} has an invalid date range.")
        source = event.get("source")
        if (
            not isinstance(source, dict)
            or source.get("source_url") != validation.get("source_url")
            or source.get("sha256") != validation.get("source_sha256")
        ):
            raise FeedError(f"Event {event_id} does not match the verified source provenance.")
        for field in ("states_included", "states_excluded"):
            states = event.get(field, [])
            if not isinstance(states, list) or not all(isinstance(state, str) for state in states):
                raise FeedError(f"Event {event_id} has an invalid {field} scope.")
            if not set(states).issubset(all_states):
                raise FeedError(f"Event {event_id} refers to an unknown state.")
        included = set(event.get("states_included", []))
        excluded = set(event.get("states_excluded", []))
        if included & excluded:
            raise FeedError(f"Event {event_id} both includes and excludes a state.")
        event_states = set(groups[group]) if group else all_states
        if included and not included.intersection(event_states):
            raise FeedError(f"Event {event_id} state scope conflicts with its KPM group.")
        if excluded and not event_states.difference(excluded):
            raise FeedError(f"Event {event_id} excludes every state in its KPM group.")
    return events, groups


def _applies_to_group(event: dict[str, Any], group: str, states: list[str]) -> bool:
    event_group = event.get("group")
    if event_group is not None and event_group != group:
        return False
    applicable_states = set(states)
    if event.get("states_included"):
        applicable_states.intersection_update(event["states_included"])
    applicable_states.difference_update(event.get("states_excluded", []))
    return bool(applicable_states)


def _school_terms(events: list[dict[str, Any]], group: str) -> list[dict[str, Any]]:
    periods = sorted(
        (
            event
            for event in events
            if event["kind"] == "schooling_period" and event["group"] == group
        ),
        key=lambda event: event["start_date"],
    )
    merged: list[dict[str, Any]] = []
    for period in periods:
        if merged and date.fromisoformat(period["start_date"]) == (
            date.fromisoformat(merged[-1]["end_date"]) + timedelta(days=1)
        ):
            merged[-1]["end_date"] = period["end_date"]
            continue
        term_number = len(merged) + 1
        merged.append(
            {
                "event_id": f"school-term-{period['academic_year']}-{group}-{term_number}",
                "academic_year": period["academic_year"],
                "kind": "school_term",
                "title": "School term",
                "group": group,
                "start_date": period["start_date"],
                "end_date": period["end_date"],
                "source": deepcopy(period["source"]),
            }
        )
    if not merged:
        raise FeedError(f"No school periods found for Group {group}.")
    return merged


def _event_summary(event: dict[str, Any], group: str) -> str:
    title = event["title"]
    if event["kind"] in {"schooling_period", "school_term"} or event.get("group"):
        title = f"{title} (Group {group})"
    if event.get("states_included"):
        title += f" ({', '.join(event['states_included'])} only)"
    elif event.get("states_excluded"):
        title += f" (excluding {', '.join(event['states_excluded'])})"
    return title


def _event_description(event: dict[str, Any], group: str, group_states: list[str]) -> str:
    lines = [
        f"Academic year: {event['academic_year']}",
        f"Applicable calendar: Group {group} ({', '.join(group_states)})",
    ]
    if event.get("states_included"):
        lines.append(f"Applies only in: {', '.join(event['states_included'])}")
    if event.get("states_excluded"):
        lines.append(f"Does not apply in: {', '.join(event['states_excluded'])}")
    if event.get("source_note"):
        lines.append(f"KPM note: {event['source_note']}")
    elif event["kind"] == "school_term":
        lines.append("Continuous school period merged from adjacent monthly rows in the KPM calendar.")
    source = event["source"]
    lines.extend(
        (
            f"Official KPM source: {source['source_url']}",
            f"PDF page: {source['page_number']}",
            f"Source SHA-256: {source['sha256']}",
        )
    )
    return "\n".join(lines)


def _calendar_bytes(
    events: list[dict[str, Any]],
    group: str,
    group_states: list[str],
    academic_year: str,
) -> bytes:
    calendar = Calendar()
    calendar.add("prodid", "-//amein14//KPM Takwim ICS//EN")
    calendar.add("version", "2.0")
    calendar.add("calscale", "GREGORIAN")
    calendar.add("method", "PUBLISH")
    calendar.add("X-WR-CALNAME", f"KPM School Calendar - Group {group} ({academic_year})")
    calendar.add(
        "X-WR-CALDESC",
        "Informational calendar based on the official KPM school calendar. "
        "State-specific exceptions are labeled; refer to KPM for authoritative dates.",
    )

    term_events = _school_terms(events, group)
    other_events = [
        event
        for event in events
        if event["kind"] != "schooling_period"
        and _applies_to_group(event, group, group_states)
    ]
    feed_events = sorted(
        [*term_events, *other_events],
        key=lambda event: (event["start_date"], event["title"], event["event_id"]),
    )
    for event_data in feed_events:
        event = Event()
        event.add("uid", f"{event_data['event_id']}@{CALENDAR_DOMAIN}")
        event.add("dtstamp", datetime.now(timezone.utc))
        event.add("dtstart", date.fromisoformat(event_data["start_date"]))
        event.add(
            "dtend",
            date.fromisoformat(event_data["end_date"]) + timedelta(days=1),
        )
        event.add("summary", _event_summary(event_data, group))
        event.add("description", _event_description(event_data, group, group_states))
        event.add("url", event_data["source"]["source_url"])
        event.add("categories", [event_data["kind"]])
        event.add("transp", "TRANSPARENT")
        calendar.add_component(event)
    return calendar.to_ical()


def generate_feeds(
    approved: dict[str, Any],
    state_groups: dict[str, Any],
) -> dict[str, bytes]:
    events, groups = _validate_inputs(approved, state_groups)
    return {
        group: _calendar_bytes(events, group, groups[group], approved["academic_year"])
        for group in ("A", "B")
    }


def build_holiday_listing(
    approved: dict[str, Any],
    state_groups: dict[str, Any],
) -> dict[str, Any]:
    events, groups = _validate_inputs(approved, state_groups)
    holidays = [
        {
            "event_id": event["event_id"],
            "kind": event["kind"],
            "title": event["title"],
            "start_date": event["start_date"],
            "end_date": event["end_date"],
            "group": event.get("group"),
            "states_included": event.get("states_included", []),
            "states_excluded": event.get("states_excluded", []),
            "applicable_groups": [
                group
                for group in ("A", "B")
                if _applies_to_group(event, group, groups[group])
            ],
            "source_note": event.get("source_note"),
            "source_url": event["source"]["source_url"],
            "page_number": event["source"]["page_number"],
        }
        for event in events
        if event["kind"] in HOLIDAY_KINDS
    ]
    return {
        "schema_version": 1,
        "status": "validated",
        "academic_year": approved["academic_year"],
        "source_sha256": approved["validation"]["source_sha256"],
        "events": holidays,
    }


def _write_feed(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            file.write(content)
            temporary_path = Path(file.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


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
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--state-groups", type=Path, default=DEFAULT_STATE_GROUPS)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--holiday-list", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        approved = _read_json(args.events, "approved events")
        state_groups = _read_json(args.state_groups, "state-group mapping")
        feeds = generate_feeds(approved, state_groups)
        holiday_listing = build_holiday_listing(approved, state_groups)
        for group, content in feeds.items():
            _write_feed(args.output_dir / f"group-{group.lower()}.ics", content)
        holiday_list_path = args.holiday_list or args.output_dir / DEFAULT_HOLIDAY_LIST.name
        _write_json(holiday_list_path, holiday_listing)
    except (FeedError, OSError) as exc:
        print(f"ICS generation failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Generated {len(feeds)} validated Group A/B feed(s) and "
        f"{len(holiday_listing['events'])} holiday list item(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
