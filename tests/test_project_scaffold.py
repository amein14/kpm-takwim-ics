import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_source_configuration_is_valid():
    data = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
    assert data["sources"]
    assert all(item["url"].startswith("https://") for item in data["sources"])


def test_events_are_validated_and_provenanced():
    data = json.loads((ROOT / "data" / "events.json").read_text(encoding="utf-8"))
    assert data["status"] == "validated"
    assert data["validation"]["status"] == "verified"
    assert data["validation"]["event_count"] == 54
    assert data["events"]
    assert all(
        event["source"]["sha256"] == data["validation"]["source_sha256"]
        for event in data["events"]
    )


def test_pages_placeholder_exists():
    assert (ROOT / "site" / "index.html").is_file()
