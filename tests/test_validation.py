from copy import deepcopy

import pytest

from scripts.validate import (
    EXPECTED_GROUP_STATES,
    ValidationError,
    _validate_calendar_coverage,
    _validate_state_groups,
)


SOURCE_URL = "https://www.moe.gov.my/calendar.pdf"
SOURCE_SHA256 = "a" * 64


def _interval(group, kind, start, end):
    return {
        "group": group,
        "kind": kind,
        "start_date": start,
        "end_date": end,
    }


def test_state_mapping_is_tied_to_the_2026_kpm_source():
    state_groups = {
        "academic_year": "2026",
        "source_url": SOURCE_URL,
        "source_sha256": SOURCE_SHA256,
        "groups": deepcopy(EXPECTED_GROUP_STATES),
    }

    assert _validate_state_groups(
        state_groups,
        "2026",
        SOURCE_URL,
        SOURCE_SHA256,
    ) == EXPECTED_GROUP_STATES

    state_groups["groups"]["A"] = ["Johor", "Kedah", "Kelantan", "Terengganu"]
    with pytest.raises(ValidationError, match="does not match"):
        _validate_state_groups(state_groups, "2026", SOURCE_URL, SOURCE_SHA256)


def test_calendar_coverage_rejects_gaps_and_overlaps():
    events = [
        _interval("A", "schooling_period", "2026-01-11", "2026-05-21"),
        _interval("A", "school_break", "2026-05-22", "2026-06-06"),
        _interval("A", "schooling_period", "2026-06-07", "2026-12-03"),
        _interval("A", "school_break", "2026-12-04", "2026-12-31"),
        _interval("B", "schooling_period", "2026-01-12", "2026-05-22"),
        _interval("B", "school_break", "2026-05-23", "2026-06-07"),
        _interval("B", "schooling_period", "2026-06-08", "2026-12-04"),
        _interval("B", "school_break", "2026-12-05", "2026-12-31"),
    ]

    _validate_calendar_coverage(events)
    events[1]["start_date"] = "2026-05-23"
    with pytest.raises(ValidationError, match="gap or overlap"):
        _validate_calendar_coverage(events)
