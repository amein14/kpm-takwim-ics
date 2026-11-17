# KPM Takwim to ICS

A community project to convert publicly available Kementerian Pendidikan Malaysia (KPM) school-calendar information into subscribable iCalendar (`.ics`) feeds.

## Project status

**Phases 3–5 — 2026 validation, ICS generation and Pages publication.** The 2026 calendar has been checked against the official KPM PDF. Group A and Group B feeds are generated during the GitHub Pages deployment. The scanned 2027 calendar remains pending OCR and is not included.

## Planned features

- Discover calendar documents published on KPM's public website.
- Download and retain source PDFs with checksums.
- Extract school-calendar events for Group A and Group B.
- Validate extracted dates before publication.
- Generate stable, subscribable ICS feeds.
- Publish the feeds and project information using GitHub Pages.

## Calendar scope

The initial scope is the KPM school calendar:
- School term dates and term breaks
- School holidays
- Additional school holidays stated in the relevant KPM calendar

Group A and Group B are kept separate. State-specific feeds may be added after the group feeds are verified.

## Repository layout

```text
.github/workflows/   GitHub Actions workflows
config/              Source and state/group configuration
data/                Staged/approved event data and source manifest
scripts/             Discovery, download, extraction and ICS scripts
site/                Static GitHub Pages website and published calendars
tests/               Automated tests
```

## Local setup

Requires Python 3.12 or later.

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
python -m pytest
```

## Phase 1: discover and download KPM PDFs

Run the source discovery and download steps locally:

```bash
python -m scripts.discover
python -m scripts.download
```

Discovery checks the KPM calendar listing and its public Takwim Persekolahan
folder. The folder uses an elFinder connector that is accessible anonymously;
the connector URL and its current page-provided anti-CSRF value are read at
runtime. No browser credentials or hard-coded cookies are used.

PDFs are stored in the ignored `data/source-pdfs/` directory. The checked-in
`data/source-manifest.json` keeps source URLs, SHA-256 checksums, timestamps and
download status (`downloaded` or `unchanged`); existing records are retained if
KPM is unavailable. Before requesting PDF bodies again, the downloader checks
public HEAD metadata and verifies the local SHA-256. The scheduled **Check KPM
sources** workflow runs tests, discovers and downloads PDFs on the runner,
checks the approved events against a fresh extraction, and publishes the
manifest and staging data as 30-day workflow artifacts. It does not update
approved events or publish newly discovered dates.
Documents without an explicit year in their filename or nearby listing text
keep a `null` year rather than an invented value. The existing GitHub Pages
deployment remains separate.

## Phase 2: extract to staging

Run the text-based calendar extractor after Phase 1 has downloaded the PDFs:

```bash
python -m scripts.extract
```

The extractor writes `data/staging-events.json` with source URLs, checksums,
page references, Group A/Group B scope, and the dates as printed in the 2026
calendar. It records the image-only 2027 PDF as `pending_ocr`. The staging file
is **unvalidated** and is not published directly. The source-check workflow
stores staging output as a separate 30-day artifact.

## Phase 3: validate the 2026 calendar

After Phase 1 has downloaded the source PDFs and Phase 2 has generated staging,
run:

```bash
python -m scripts.validate
```

Validation checks the official PDF checksum, reproduces the extraction,
verifies all 54 reviewed event records and their source references, checks
continuous Group A/Group B school and break intervals, and uses the state
assignments shown in the 2026 KPM document. The checked-in
`data/events.json` records this validation. Group A is Kedah, Kelantan and
Terengganu; Johor is in Group B for the 2026 calendar. State-specific
exceptions remain labeled.

The scanned 2027 calendar is not validated and must not be inferred from 2026.

## Phase 4: generate ICS feeds

Generate the two feeds locally:

```bash
python -m scripts.generate_ics --output-dir site
```

The script refuses data that is not marked validated and whose state mapping
or source checksum differs from the validation record. It creates
`site/group-a.ics` and `site/group-b.ics`; continuous monthly school-period
rows are merged into term spans. All-day event end dates follow iCalendar's
exclusive-end convention, stable UIDs are used, and state-specific exceptions
are described in the events.

## Phase 5: publish verified feeds

Push the approved event data, feed generator and site changes to `main`. The
**Deploy GitHub Pages** workflow runs the test suite, generates the feeds into
the Pages build artifact, and deploys the site. The feed links appear on the
homepage after the deployment succeeds. The workflow never republishes
unvalidated staging data.

## GitHub setup

1. Create a **public** repository named `kpm-takwim-ics`.
2. Upload or push the contents of this project to the repository's `main` branch.
3. In GitHub, open **Settings → Pages**.
4. Under **Build and deployment → Source**, select **GitHub Actions**.
5. Open **Actions** and enable workflows if prompted.
6. Run **Deploy GitHub Pages** manually once, or push a change to `site/`.
7. After deployment, GitHub will show the Pages URL under **Settings → Pages**.

The deployment workflow runs tests, generates the two feeds in the Pages
artifact, then deploys the site. Generated `.ics` files are not committed.
Their published URLs are `group-a.ics` and `group-b.ics` at the Pages site root.
The workflow uses GitHub's Pages artifact/deployment process and does not
require a personal access token or repository secret.

## Data safety and publication policy

- The scheduled source-check workflow never replaces validated events. If the official source changes, review and validate the new version before publishing it.
- Keep the original source document URL, retrieval timestamp and SHA-256 checksum with each source record.
- Preserve event UIDs between calendar revisions so subscribers receive updates instead of duplicate events.
- Group feeds label state-specific exceptions; they are not state-tailored calendars.
- Never commit browser session cookies, login credentials, API keys or personal access tokens.
- The public feeds are informational and should link back to the official KPM source. KPM remains the authoritative source.

## Development roadmap

1. Phase 0: repository and Pages foundation.
2. Phase 1: discover and download public KPM calendar PDFs.
3. Phase 2: extract 2026 dates into staging; 2027 awaits OCR.
4. Phase 3: validate Group A and Group B dates — complete for the 2026 source.
5. Phase 4: generate and test ICS feeds — complete.
6. Phase 5: publish verified feeds using GitHub Pages — deployment runs on push.
7. Phase 6: scheduled monitoring and review workflow.

## License

Choose and add a license before accepting contributions or redistributing source documents. The project does not claim ownership of KPM publications.
