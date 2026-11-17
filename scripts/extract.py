"""Extract 2026 KPM calendar dates into an unvalidated staging file."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf

from scripts.download import sha256_file
from scripts.discover import ROOT

DEFAULT_MANIFEST = ROOT / "data" / "source-manifest.json"
DEFAULT_PDF_DIR = ROOT / "data" / "source-pdfs"
DEFAULT_OUTPUT = ROOT / "data" / "staging-events.json"
DATE_PATTERN = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
DATE_RANGE_PATTERN = re.compile(
    r"^(\d{2}\.\d{2}\.\d{4})\s*[-–]\s*(\d{2}\.\d{2}\.\d{4})$"
)
DATE_TOKEN = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
FESTIVAL_HEADINGS = (
    ("TAHUN BARU CINA", "Tahun Baru Cina"),
    ("HARI RAYA AIDILFITRI", "Hari Raya Aidilfitri"),
    ("HARI DEEPAVALI", "Hari Deepavali"),
)
FOOTNOTE_FESTIVALS = (
    ("Pesta Kaamatan", re.compile(r"pesta\s+kaamatan", re.IGNORECASE)),
    ("Perayaan Hari Gawai Dayak", re.compile(r"perayaan\s+hari\s+gawai\s+dayak", re.IGNORECASE)),
    ("Perayaan Hari Krismas", re.compile(r"perayaan\s+hari\s+krismas", re.IGNORECASE)),
)
MONTHS = {
    "januari": 1,
    "februari": 2,
    "mac": 3,
    "april": 4,
    "mei": 5,
    "jun": 6,
    "julai": 7,
    "ogos": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "disember": 12,
}
FOOTNOTE_DATES = re.compile(
    r"(?<!\d)(\d{1,2})(?:\s*&\s*(\d{1,2}))?\s+"
    r"(Januari|Februari|Mac|April|Mei|Jun|Julai|Ogos|September|Oktober|November|Disember)"
    r"\s+(20\d{2})",
    re.IGNORECASE,
)


class ExtractionError(RuntimeError):
    """Raised when a source PDF cannot be safely extracted."""


def _iso_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%d.%m.%Y").date().isoformat()
    except ValueError as exc:
        raise ExtractionError(f"Invalid date in KPM PDF: {value}") from exc


def _make_event(
    *,
    academic_year: str,
    kind: str,
    title: str,
    group: str | None,
    start_date: str,
    end_date: str,
    page_number: int,
    source_section: str,
    source_text: str,
    document: dict[str, Any],
    source_row_index: int = 0,
    states_included: list[str] | None = None,
    states_excluded: list[str] | None = None,
    source_note: str | None = None,
) -> dict[str, Any]:
    if start_date[:4] != academic_year or end_date[:4] != academic_year or start_date > end_date:
        raise ExtractionError(f"Event dates do not fit academic year {academic_year}: {start_date} - {end_date}.")
    scope = {
        "group": group,
        "states_included": states_included or [],
        "states_excluded": states_excluded or [],
    }
    identity = "|".join(
        (
            academic_year,
            kind,
            title,
            group or "all",
            str(page_number),
            source_section,
            str(source_row_index),
        )
    )
    event = {
        "event_id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20],
        "academic_year": academic_year,
        "kind": kind,
        "title": title,
        "group": group,
        "start_date": start_date,
        "end_date": end_date,
        "source": {
            "filename": document["filename"],
            "source_url": document["source_url"],
            "sha256": document["sha256"],
            "page_number": page_number,
            "section": source_section,
            "text": source_text,
        },
    }
    if scope["states_included"]:
        event["states_included"] = scope["states_included"]
    if scope["states_excluded"]:
        event["states_excluded"] = scope["states_excluded"]
    if source_note:
        event["source_note"] = source_note
    return event


def _extract_group_page(
    page: Any,
    page_number: int,
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    text = page.get_text("text")
    group_match = re.search(r"\bKUMPULAN\s+([AB])\b", text, re.IGNORECASE)
    if not group_match:
        raise ExtractionError(f"Page {page_number} has no explicit KUMPULAN A/B heading.")
    group = group_match.group(1).upper()
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    date_lines = [line for line in lines if DATE_PATTERN.fullmatch(line)]
    if not date_lines or len(date_lines) % 2:
        raise ExtractionError(
            f"Page {page_number} has an unexpected number of school-period date cells."
        )

    events: list[dict[str, Any]] = []
    for index in range(0, len(date_lines), 2):
        start_text, end_text = date_lines[index:index + 2]
        start_date, end_date = _iso_date(start_text), _iso_date(end_text)
        if start_date[:4] != "2026" or end_date[:4] != "2026" or start_date > end_date:
            raise ExtractionError(f"Page {page_number} contains an out-of-scope school-period date.")
        events.append(
            _make_event(
                academic_year="2026",
                kind="schooling_period",
                title="School period",
                group=group,
                start_date=start_date,
                end_date=end_date,
                page_number=page_number,
                source_section=f"KALENDAR AKADEMIK TAHUN 2026 / KUMPULAN {group}",
                source_text=f"{start_text} - {end_text}",
                document=document,
                source_row_index=index // 2,
            )
        )

    for index, line in enumerate(lines):
        if not line.upper().startswith("CUTI "):
            continue
        date_range = next(
            (
                match
                for candidate in lines[index + 1:index + 5]
                if (match := DATE_RANGE_PATTERN.fullmatch(candidate))
            ),
            None,
        )
        if not date_range:
            raise ExtractionError(f"Could not find a date range following {line!r} on page {page_number}.")
        start_text, end_text = date_range.groups()
        start_date, end_date = _iso_date(start_text), _iso_date(end_text)
        if start_date[:4] != "2026" or end_date[:4] != "2026" or start_date > end_date:
            raise ExtractionError(f"Page {page_number} contains an out-of-scope school-break date.")
        events.append(
            _make_event(
                academic_year="2026",
                kind="school_break",
                title=line,
                group=group,
                start_date=start_date,
                end_date=end_date,
                page_number=page_number,
                source_section=line,
                source_text=f"{start_text} - {end_text}",
                document=document,
            )
        )
    return events


def _word_lines(words: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for word in sorted(words, key=lambda item: (item[1], item[0])):
        y0 = word[1]
        if lines and abs(lines[-1]["y"] - y0) <= 1.5:
            lines[-1]["words"].append(word)
        else:
            lines.append({"y": y0, "words": [word]})
    for line in lines:
        line["words"].sort(key=lambda item: item[0])
        line["text"] = " ".join(str(word[4]) for word in line["words"])
    return lines


def _date_in_word(word: tuple[Any, ...]) -> bool:
    return bool(DATE_PATTERN.fullmatch(str(word[4])))


def _state_scope(note: str) -> tuple[list[str], list[str]]:
    normalized = " ".join(note.casefold().split())
    if "negeri sarawak sahaja" in normalized:
        return ["Sarawak"], []
    if "kecuali negeri sarawak" in normalized or "kecuali sarawak" in normalized:
        return [], ["Sarawak"]
    if "sabah" in normalized and "labuan" in normalized and "sahaja" in normalized:
        return ["Sabah", "Wilayah Persekutuan Labuan"], []
    if "sarawak sahaja" in normalized:
        return ["Sarawak"], []
    return [], []


def _column_dates(
    words: list[tuple[Any, ...]],
    x_min: float,
    x_max: float,
    y_min: float,
    y_max: float,
) -> list[tuple[float, str]]:
    return sorted(
        (
            (float(word[1]), str(word[4]))
            for word in words
            if x_min <= float(word[0]) < x_max
            and y_min <= float(word[1]) < y_max
            and _date_in_word(word)
        ),
        key=lambda item: item[0],
    )


def _extract_festival_table(
    page: Any,
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    words = page.get_text("words", sort=True)
    lines = _word_lines(words)
    headings: list[tuple[float, str]] = []
    for line in lines:
        if min(float(word[0]) for word in line["words"]) >= 200 or line["y"] >= 500:
            continue
        normalized = " ".join(line["text"].upper().split())
        for marker, title in FESTIVAL_HEADINGS:
            if marker in normalized:
                headings.append((line["y"], title))
                break
    if [title for _, title in headings] != [title for _, title in FESTIVAL_HEADINGS]:
        raise ExtractionError("The 2026 festive-holiday page has an unexpected heading/layout.")

    # KPM places the table headings within merged cells; each next section starts 30 points above its heading.
    boundaries = [max(0.0, headings[0][0] - 30.0)]
    boundaries.extend(y - 30.0 for y, _ in headings[1:])
    boundaries.append(float(page.rect.height))

    events: list[dict[str, Any]] = []
    columns = (
        (None, 70.0, 200.0),
        ("A", 200.0, 300.0),
        ("B", 300.0, 420.0),
    )
    for section_index, (_, title) in enumerate(headings):
        y_min, y_max = boundaries[section_index:section_index + 2]
        for group, x_min, x_max in columns:
            dates = _column_dates(words, x_min, x_max, y_min, y_max)
            for date_index, (y, date_text) in enumerate(dates):
                next_y = dates[date_index + 1][0] if date_index + 1 < len(dates) else y_max
                next_y = min(next_y, y + 60.0)
                note = " ".join(
                    str(word[4])
                    for word in words
                    if x_min <= float(word[0]) < x_max
                    and y <= float(word[1]) < next_y
                    and not _date_in_word(word)
                )
                states_included, states_excluded = _state_scope(note)
                is_additional = group is not None
                events.append(
                    _make_event(
                        academic_year="2026",
                        kind="additional_kpm_holiday" if is_additional else "festival_observance",
                        title=f"Cuti tambahan KPM: {title}" if is_additional else title,
                        group=group,
                        start_date=_iso_date(date_text),
                        end_date=_iso_date(date_text),
                        page_number=3,
                        source_section=(
                            f"TAMBAHAN CUTI PERAYAAN / KUMPULAN {group}"
                            if group
                            else "CUTI PERAYAAN"
                        ),
                        source_text=date_text,
                        document=document,
                        source_row_index=date_index,
                        states_included=states_included,
                        states_excluded=states_excluded,
                        source_note=" ".join(note.split()) if states_included or states_excluded else None,
                    )
                )
    return events


def _extract_footnote_festivals(
    page: Any,
    document: dict[str, Any],
) -> list[dict[str, Any]]:
    words = page.get_text("words", sort=True)
    lines = _word_lines(words)
    festival_lines = [
        (title, pattern, index)
        for title, pattern in FOOTNOTE_FESTIVALS
        for index, line in enumerate(lines)
        if pattern.search(line["text"]) and line["y"] >= 600
    ]
    events: list[dict[str, Any]] = []
    found: set[str] = set()
    for title, _, index in festival_lines:
        found.add(title)
        line = lines[index]
        date_match = FOOTNOTE_DATES.search(line["text"])
        if not date_match:
            raise ExtractionError(f"Could not read the date on the same row as {title} in the 2026 calendar.")
        first_day, second_day, month_name, year = date_match.groups()
        month = MONTHS[month_name.casefold()]
        start_date = datetime(int(year), month, int(first_day)).date().isoformat()
        end_date = datetime(int(year), month, int(second_day or first_day)).date().isoformat()
        following_festival_y = min(
            (
                other_line["y"]
                for other_title, other_pattern in FOOTNOTE_FESTIVALS
                if other_title != title
                for other_line in lines
                if other_line["y"] > line["y"] and other_pattern.search(other_line["text"])
            ),
            default=float(page.rect.height),
        )
        note = " ".join(
            str(word[4])
            for word in words
            if float(word[0]) < 250 and line["y"] <= float(word[1]) < following_festival_y
        )
        states_included, states_excluded = _state_scope(note)
        events.append(
            _make_event(
                academic_year="2026",
                kind="festival_observance",
                title=title,
                group=None,
                start_date=start_date,
                end_date=end_date,
                page_number=3,
                source_section=f"CATATAN / {title}",
                source_text=line["text"],
                document=document,
                source_row_index=0,
                states_included=states_included,
                states_excluded=states_excluded,
                source_note=" ".join(note.split()) if states_included or states_excluded else None,
            )
        )
    expected = {title for title, _ in FOOTNOTE_FESTIVALS}
    if found != expected:
        missing = ", ".join(sorted(expected - found))
        raise ExtractionError(f"2026 calendar notes are missing expected festival entries: {missing}.")
    return events


def extract_2026_events(pages: list[Any], document: dict[str, Any]) -> list[dict[str, Any]]:
    if len(pages) < 3:
        raise ExtractionError("The 2026 calendar PDF is missing its expected A/B tables or festival appendix.")
    events = _extract_group_page(pages[0], 1, document)
    events.extend(_extract_group_page(pages[1], 2, document))
    events.extend(_extract_festival_table(pages[2], document))
    events.extend(_extract_footnote_festivals(pages[2], document))
    groups = {event["group"] for event in events if event["kind"] == "schooling_period"}
    if groups != {"A", "B"}:
        raise ExtractionError("The 2026 calendar did not yield separate schooling periods for Groups A and B.")
    return sorted(
        events,
        key=lambda event: (
            event["start_date"],
            event["group"] or "",
            event["kind"],
            event["title"],
            event["source"]["page_number"],
        ),
    )


def _open_pdf(path: Path) -> pymupdf.Document:
    try:
        return pymupdf.open(path)
    except (OSError, RuntimeError) as exc:
        raise ExtractionError(f"Unable to open source PDF {path}: {exc}") from exc


def _manifest_document(manifest: dict[str, Any], academic_year: str) -> dict[str, Any]:
    match = next(
        (
            item
            for item in manifest["documents"]
            if isinstance(item, dict) and item.get("academic_year") == academic_year
        ),
        None,
    )
    if match is None:
        raise ExtractionError(f"No source-manifest record exists for academic year {academic_year}.")
    return match


def _read_verified_pdf(document: dict[str, Any], pdf_dir: Path) -> pymupdf.Document:
    filename = document.get("filename")
    expected_hash = document.get("sha256")
    if not isinstance(filename, str) or not isinstance(expected_hash, str):
        raise ExtractionError("A source-manifest PDF record is missing its filename or SHA-256.")
    path = pdf_dir / filename
    if not path.is_file():
        raise ExtractionError(f"Source PDF is not downloaded: {path}")
    if sha256_file(path) != expected_hash:
        raise ExtractionError(f"SHA-256 mismatch for source PDF {filename}.")
    return _open_pdf(path)


def _document_status(
    source: dict[str, Any],
    pdf_dir: Path,
    *,
    require_scanned: bool = False,
) -> dict[str, Any]:
    pdf = _read_verified_pdf(source, pdf_dir)
    try:
        text_page_count = sum(bool(page.get_text("text").strip()) for page in pdf)
        image_page_count = sum(bool(page.get_images(full=True)) for page in pdf)
        result = {
            "academic_year": source["academic_year"],
            "filename": source["filename"],
            "source_url": source["source_url"],
            "sha256": source["sha256"],
            "page_count": pdf.page_count,
        }
        if require_scanned and text_page_count == 0 and image_page_count == pdf.page_count:
            result.update(
                {
                    "extraction_status": "pending_ocr",
                    "reason": "All pages are image-only; OCR is not configured for this extraction run.",
                }
            )
        elif text_page_count == 0:
            result.update(
                {
                    "extraction_status": "pending_review",
                    "reason": "No extractable text or full-page scan images were found.",
                }
            )
        else:
            result.update(
                {
                    "extraction_status": "pending_review",
                    "reason": (
                        "This Phase 2 parser does not extract this academic-year layout."
                        if text_page_count
                        else "No extractable text was found."
                    ),
                }
            )
        return result
    finally:
        pdf.close()


def build_staging_data(manifest_path: Path, pdf_dir: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtractionError(f"Unable to read source manifest {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("documents"), list):
        raise ExtractionError(f"Source manifest {manifest_path} has an invalid document list.")

    selected = _manifest_document(manifest, "2026")
    source_document = _read_verified_pdf(selected, pdf_dir)
    try:
        if not all(page.get_text("text").strip() for page in source_document):
            raise ExtractionError("The selected 2026 PDF contains image-only pages; text extraction is unsafe.")
        events = extract_2026_events(list(source_document), selected)
        extracted_record = {
            "academic_year": "2026",
            "filename": selected["filename"],
            "source_url": selected["source_url"],
            "sha256": selected["sha256"],
            "page_count": source_document.page_count,
            "extraction_status": "extracted_unvalidated",
            "event_count": len(events),
        }
    finally:
        source_document.close()

    pending_records = []
    pending = next(
        (
            item
            for item in manifest["documents"]
            if isinstance(item, dict) and item.get("academic_year") == "2027"
        ),
        None,
    )
    if pending is not None:
        pending_records.append(_document_status(pending, pdf_dir, require_scanned=True))
    return {
        "schema_version": 1,
        "status": "staging_only_unvalidated",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "documents": [extracted_record, *pending_records],
        "events": events,
        "review_note": (
            "Extracted values are unvalidated staging data. Do not copy them into data/events.json "
            "or publish them until Phase 3 review is complete."
        ),
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
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    try:
        staging = build_staging_data(args.manifest, args.pdf_dir)
        _write_json(args.output, staging)
    except (ExtractionError, OSError) as exc:
        print(f"Extraction failed: {exc}", file=sys.stderr)
        return 1
    print(f"Staged {len(staging['events'])} unvalidated event(s) in {args.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
