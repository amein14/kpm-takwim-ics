import json
from datetime import date

import pytest
from icalendar import Calendar

from scripts.data_integrity import events_sha256
from scripts.generate_ics import FeedError, build_holiday_listing, generate_feeds, main


SOURCE_URL = "https://www.moe.gov.my/calendar.pdf"
SOURCE_SHA256 = "a" * 64
GROUPS = {
    "A": ["Kedah", "Kelantan", "Terengganu"],
    "B": ["Johor", "Sabah", "Sarawak"],
}


def _event(event_id, kind, title, group, start, end, **scope):
    event = {
        "event_id": event_id,
        "academic_year": "2026",
        "kind": kind,
        "title": title,
        "group": group,
        "start_date": start,
        "end_date": end,
        "source": {
            "filename": "calendar.pdf",
            "source_url": SOURCE_URL,
            "sha256": SOURCE_SHA256,
            "page_number": 1,
        },
    }
    event.update(scope)
    return event


@pytest.fixture
def feed_inputs():
    events = [
        _event("a-jan", "schooling_period", "School period", "A", "2026-01-11", "2026-01-31"),
        _event("a-feb", "schooling_period", "School period", "A", "2026-02-01", "2026-02-28"),
        _event("b-jan", "schooling_period", "School period", "B", "2026-01-12", "2026-01-31"),
        _event("a-break", "school_break", "CUTI PENGGAL 1", "A", "2026-03-20", "2026-03-28"),
        _event("b-break", "school_break", "CUTI PENGGAL 1", "B", "2026-03-21", "2026-03-29"),
        _event("a-holiday", "additional_kpm_holiday", "Cuti KPM", "A", "2026-03-19", "2026-03-19"),
        _event(
            "b-sarawak-holiday",
            "additional_kpm_holiday",
            "Deepavali",
            "B",
            "2026-11-09",
            "2026-11-09",
            states_included=["Sarawak"],
            source_note="Negeri Sarawak sahaja",
        ),
        _event(
            "b-except-sarawak",
            "additional_kpm_holiday",
            "Deepavali",
            "B",
            "2026-11-10",
            "2026-11-10",
            states_excluded=["Sarawak"],
            source_note="Semua Negeri Kumpulan B kecuali Negeri Sarawak",
        ),
        _event(
            "kaamatan",
            "festival_observance",
            "Pesta Kaamatan",
            None,
            "2026-05-30",
            "2026-05-31",
            states_included=["Sabah"],
        ),
        _event(
            "deepavali",
            "festival_observance",
            "Hari Deepavali",
            None,
            "2026-11-08",
            "2026-11-08",
            states_excluded=["Sarawak"],
        ),
        _event("christmas", "festival_observance", "Christmas", None, "2026-12-25", "2026-12-25"),
    ]
    approved = {
        "schema_version": 1,
        "academic_year": "2026",
        "status": "validated",
        "validation": {
            "status": "verified",
            "source_url": SOURCE_URL,
            "source_sha256": SOURCE_SHA256,
            "group_states": GROUPS,
            "event_count": len(events),
            "events_sha256": events_sha256(events),
        },
        "events": events,
    }
    state_groups = {
        "academic_year": "2026",
        "source_url": SOURCE_URL,
        "source_sha256": SOURCE_SHA256,
        "groups": GROUPS,
    }
    return approved, state_groups


def _vevents(content):
    return [
        component
        for component in Calendar.from_ical(content).walk()
        if component.name == "VEVENT"
    ]


def test_group_feeds_keep_state_exceptions_and_merge_school_rows(feed_inputs):
    approved, state_groups = feed_inputs
    feeds = generate_feeds(approved, state_groups)
    group_a = _vevents(feeds["A"])
    group_b = _vevents(feeds["B"])
    summaries_a = [str(event["summary"]) for event in group_a]
    summaries_b = [str(event["summary"]) for event in group_b]

    term_a = next(event for event in group_a if "School term" in str(event["summary"]))
    assert term_a["dtstart"].dt == date(2026, 1, 11)
    assert term_a["dtend"].dt == date(2026, 3, 1)
    assert term_a["transp"] == "TRANSPARENT"
    assert any("Cuti KPM (Group A)" in summary for summary in summaries_a)
    assert not any("Pesta Kaamatan" in summary for summary in summaries_a)
    assert any("Hari Deepavali (excluding Sarawak)" in summary for summary in summaries_a)

    assert any("Deepavali (Group B) (Sarawak only)" in summary for summary in summaries_b)
    assert any("Deepavali (Group B) (excluding Sarawak)" in summary for summary in summaries_b)
    assert any("Pesta Kaamatan (Sabah only)" in summary for summary in summaries_b)
    assert any("Christmas" in summary for summary in summaries_a)
    assert any("Christmas" in summary for summary in summaries_b)
    assert len({str(event["uid"]) for event in group_a}) == len(group_a)
    assert all(event["dtstart"].dt.__class__ is date for event in group_a)


def test_generator_rejects_unvalidated_data_and_changed_group_map(feed_inputs):
    approved, state_groups = feed_inputs
    approved["status"] = "staging_only_unvalidated"
    with pytest.raises(FeedError, match="not marked validated"):
        generate_feeds(approved, state_groups)

    approved["status"] = "validated"
    state_groups["groups"] = {"A": ["Johor"], "B": ["Kedah"]}
    with pytest.raises(FeedError, match="does not match"):
        generate_feeds(approved, state_groups)

    state_groups["groups"] = GROUPS
    approved["events"][0]["start_date"] = "2026-01-10"
    with pytest.raises(FeedError, match="integrity record"):
        generate_feeds(approved, state_groups)


def test_holiday_list_contains_breaks_and_holidays_but_not_school_periods(feed_inputs):
    approved, state_groups = feed_inputs
    listing = build_holiday_listing(approved, state_groups)

    assert listing["status"] == "validated"
    assert len(listing["events"]) == 8
    assert "schooling_period" not in {event["kind"] for event in listing["events"]}
    assert {"school_break", "additional_kpm_holiday", "festival_observance"} == {
        event["kind"] for event in listing["events"]
    }
    assert all(event["source_url"] == SOURCE_URL for event in listing["events"])


def test_generator_writes_holiday_list_for_the_pages_build(feed_inputs, tmp_path):
    approved, state_groups = feed_inputs
    events_path = tmp_path / "events.json"
    groups_path = tmp_path / "state-groups.json"
    output_dir = tmp_path / "site"
    events_path.write_text(json.dumps(approved), encoding="utf-8")
    groups_path.write_text(json.dumps(state_groups), encoding="utf-8")

    assert main(
        [
            "--events",
            str(events_path),
            "--state-groups",
            str(groups_path),
            "--output-dir",
            str(output_dir),
        ]
    ) == 0
    listing = json.loads((output_dir / "school-holidays.json").read_text(encoding="utf-8"))
    assert listing["status"] == "validated"
    assert len(listing["events"]) == 8
    assert (output_dir / "group-a.ics").is_file()
    assert (output_dir / "group-b.ics").is_file()
