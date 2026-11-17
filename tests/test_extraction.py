from types import SimpleNamespace

from scripts.extract import (
    _extract_festival_table,
    _extract_footnote_festivals,
    _extract_group_page,
)


DOCUMENT = {
    "filename": "Kalendar-Akademik-2026.pdf",
    "source_url": "https://www.moe.gov.my/calendar.pdf",
    "sha256": "source-checksum",
}


def _word(text, x, y):
    width = max(8, len(text) * 4)
    return (x, y, x + width, y + 8, text, 0, 0, 0)


def _words_line(text, x, y, step=8):
    return [_word(value, x + index * step, y) for index, value in enumerate(text.split())]


class FakePage:
    def __init__(self, text="", words=None, height=842):
        self.text = text
        self.words = words or []
        self.rect = SimpleNamespace(height=height)

    def get_text(self, option, **_kwargs):
        if option == "text":
            return self.text
        if option == "words":
            return sorted(self.words, key=lambda word: (word[1], word[0])) if _kwargs.get("sort") else self.words
        raise AssertionError(f"Unexpected extraction mode: {option}")


def test_extract_school_periods_and_breaks_with_distinct_group_dates():
    page_a = FakePage(
        text=(
            "KALENDAR AKADEMIK TAHUN 2026\n"
            "KUMPULAN A:\n"
            "11.01.2026\n31.01.2026\n"
            "01.02.2026\n28.02.2026\n"
            "CUTI PENGGAL 1, TAHUN 2026\n"
            "1\n20.03.2026 - 28.03.2026\n"
        )
    )
    page_b = FakePage(
        text=(
            "KALENDAR AKADEMIK TAHUN 2026\n"
            "KUMPULAN B:\n"
            "12.01.2026\n31.01.2026\n"
            "01.02.2026\n28.02.2026\n"
            "CUTI PENGGAL 1, TAHUN 2026\n"
            "1\n21.03.2026 - 29.03.2026\n"
        )
    )

    events_a = _extract_group_page(page_a, 1, DOCUMENT)
    events_b = _extract_group_page(page_b, 2, DOCUMENT)

    assert [(event["start_date"], event["end_date"]) for event in events_a[:2]] == [
        ("2026-01-11", "2026-01-31"),
        ("2026-02-01", "2026-02-28"),
    ]
    assert events_a[-1]["kind"] == "school_break"
    assert events_a[-1]["group"] == "A"
    assert (events_a[-1]["start_date"], events_a[-1]["end_date"]) == (
        "2026-03-20",
        "2026-03-28",
    )
    assert events_b[-1]["group"] == "B"
    assert (events_b[-1]["start_date"], events_b[-1]["end_date"]) == (
        "2026-03-21",
        "2026-03-29",
    )
    revised_page_a = FakePage(
        text=(
            "KALENDAR AKADEMIK TAHUN 2026\n"
            "KUMPULAN A:\n"
            "12.01.2026\n31.01.2026\n"
            "01.02.2026\n28.02.2026\n"
            "CUTI PENGGAL 1, TAHUN 2026\n"
            "1\n20.03.2026 - 28.03.2026\n"
        )
    )
    revised_events_a = _extract_group_page(revised_page_a, 1, DOCUMENT)
    assert revised_events_a[0]["event_id"] == events_a[0]["event_id"]


def test_extract_festival_dates_and_deepavali_state_exceptions():
    words = []
    words += _words_line("TAHUN BARU CINA", 100, 170)
    words += _words_line("HARI RAYA AIDILFITRI", 100, 289)
    words += _words_line("HARI DEEPAVALI", 100, 415)

    for date, y in (("17.02.2026", 193), ("18.02.2026", 220)):
        words.append(_word(date, 100, y))
    for date, y in (("15.02.2026", 152), ("16.02.2026", 197), ("19.02.2026", 240)):
        words.append(_word(date, 230, y))
    for date, y in (("16.02.2026", 152), ("19.02.2026", 197), ("20.02.2026", 240)):
        words.append(_word(date, 350, y))

    for date, y in (("21.03.2026", 312), ("22.03.2026", 339)):
        words.append(_word(date, 100, y))
    words.append(_word("19.03.2026", 230, 289))
    words.extend((_word("19.03.2026", 350, 289), _word("20.03.2026", 350, 342)))

    words.append(_word("08.11.2026", 100, 432))
    words += _words_line("(kecuali Negeri Sarawak)", 100, 448)
    words.append(_word("09.11.2026", 230, 425))
    words.append(_word("10.11.2026", 350, 387))
    words += _words_line("Semua Negeri", 350, 407, step=5)
    words += _words_line("Kumpulan B kecuali", 350, 418, step=5)
    words += _words_line("Negeri Sarawak", 350, 430, step=5)
    words.append(_word("09.11.2026", 350, 465))
    words += _words_line("Negeri Sarawak sahaja", 350, 476, step=5)

    events = _extract_festival_table(FakePage(words=words), DOCUMENT)
    chinese_new_year_b = [
        event["start_date"]
        for event in events
        if event["kind"] == "additional_kpm_holiday"
        and event["group"] == "B"
        and "Tahun Baru Cina" in event["title"]
    ]
    deepavali_general = next(
        event for event in events if event["title"] == "Hari Deepavali"
    )
    deepavali_b = [
        event for event in events
        if event["group"] == "B" and "Hari Deepavali" in event["title"]
    ]

    assert chinese_new_year_b == ["2026-02-16", "2026-02-19", "2026-02-20"]
    assert deepavali_general["states_excluded"] == ["Sarawak"]
    assert deepavali_general["source_note"] == "(kecuali Negeri Sarawak)"
    assert [
        (
            event["start_date"],
            event.get("states_included"),
            event.get("states_excluded"),
            event.get("source_note"),
        )
        for event in sorted(deepavali_b, key=lambda event: event["start_date"])
    ] == [
        ("2026-11-09", ["Sarawak"], None, "Negeri Sarawak sahaja"),
        (
            "2026-11-10",
            None,
            ["Sarawak"],
            "Semua Negeri Kumpulan B kecuali Negeri Sarawak",
        ),
    ]


def test_extract_state_scoped_festival_notes_from_same_pdf_rows():
    lines = [
        *_words_line("Pesta Kaamatan : 30 & 31 Mei 2026", 68, 681),
        *_words_line("(Sabah dan Wilayah Persekutuan", 68, 693),
        *_words_line("Labuan sahaja)", 68, 704),
        *_words_line("Perayaan Hari Gawai Dayak : 01 & 02 Jun 2026", 68, 720),
        *_words_line("(Sarawak sahaja)", 68, 731),
        *_words_line("Perayaan Hari Krismas : 25 Disember 2026", 68, 755),
    ]

    events = _extract_footnote_festivals(FakePage(words=lines), DOCUMENT)

    assert [(event["title"], event["start_date"], event["end_date"]) for event in events] == [
        ("Pesta Kaamatan", "2026-05-30", "2026-05-31"),
        ("Perayaan Hari Gawai Dayak", "2026-06-01", "2026-06-02"),
        ("Perayaan Hari Krismas", "2026-12-25", "2026-12-25"),
    ]
    assert events[0]["states_included"] == ["Sabah", "Wilayah Persekutuan Labuan"]
    assert events[1]["states_included"] == ["Sarawak"]
