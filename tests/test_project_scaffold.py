import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_source_configuration_is_valid():
    data = json.loads((ROOT / "config" / "sources.json").read_text(encoding="utf-8"))
    assert data["sources"]
    assert all(item["url"].startswith("https://") for item in data["sources"])


def test_events_start_empty_and_unpublished():
    data = json.loads((ROOT / "data" / "events.json").read_text(encoding="utf-8"))
    assert data["status"] == "empty"
    assert data["events"] == []


def test_pages_placeholder_exists():
    assert (ROOT / "site" / "index.html").is_file()
