# Processing scripts

Phase 1 discovers PDFs from the KPM calendar listing and the public Takwim Persekolahan elFinder folder:

```bash
python -m scripts.discover
python -m scripts.download
```

Discovery writes an ignored `data/discovered-documents.json` intermediate file.
Downloads are saved under the ignored `data/source-pdfs/` directory, while
`data/source-manifest.json` records each document's source URL, checksum,
retrieval time and status. Remote HEAD metadata and the local checksum are
checked before downloading a PDF again. KPM's anonymous elFinder token is read
from the public folder page at runtime and is never written to the manifest.

The scripts validate PDF content and retain existing manifest records when a
source is temporarily unavailable. Discovery, downloading, PDF extraction,
event validation and ICS generation remain separate steps. Phase 1 only
discovers and downloads sources; Phase 2 extraction is described below.

## Phase 2 extraction

After downloading the PDFs, run:

```bash
python -m scripts.extract
```

This extracts the 2026 calendar into `data/staging-events.json`, keeping the
document's Group A and Group B rows separate and retaining page/text
provenance. The scanned 2027 PDF is marked `pending_ocr`. Staged dates are not
published directly.

## Phase 3 validation

After the 2026 PDF is downloaded and staging is generated, validate the reviewed
dates against the exact source document:

```bash
python -m scripts.validate
```

This refreshes `data/events.json` only if the official PDF checksum, extracted
events, expected group mapping and date coverage pass. The 2026 mapping places
Johor in Group B. A changed KPM source requires review; the scheduled source
check never silently replaces the approved event data.

## Phase 4 ICS generation

Generate the published Group A and Group B calendars:

```bash
python -m scripts.generate_ics --output-dir site
```

Only validated data is accepted. School-period month rows are combined into
continuous term spans, while breaks and holiday exceptions remain individual
all-day events with KPM source links and stable UIDs.

## Phase 5 GitHub Pages publication

The Deploy GitHub Pages workflow runs the test suite, generates the two feeds,
and deploys them with the static site. The generated `.ics` files are build
outputs and are not committed to the repository.
