# KPM Takwim to ICS

A community project to convert publicly available Kementerian Pendidikan Malaysia (KPM) school-calendar information into subscribable iCalendar (`.ics`) feeds.

## Project status

**Phase 0 — repository foundation.** The current site is a placeholder. No official KPM dates have been extracted or published yet.

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
data/                Approved event data and source manifest
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

## GitHub setup

1. Create a **public** repository named `kpm-takwim-ics`.
2. Upload or push the contents of this project to the repository's `main` branch.
3. In GitHub, open **Settings → Pages**.
4. Under **Build and deployment → Source**, select **GitHub Actions**.
5. Open **Actions** and enable workflows if prompted.
6. Run **Deploy GitHub Pages** manually once, or push a change to `site/`.
7. After deployment, GitHub will show the Pages URL under **Settings → Pages**.

The deployment workflow uses GitHub's Pages artifact/deployment process. It does not require a personal access token or repository secret.

## Data safety and publication policy

- Do not publish extracted dates automatically until parsing and validation are implemented and reviewed.
- Keep the original source document URL, retrieval timestamp and SHA-256 checksum with each source record.
- Preserve event UIDs between calendar revisions so subscribers receive updates instead of duplicate events.
- Never commit browser session cookies, login credentials, API keys or personal access tokens.
- The public feeds are informational and should link back to the official KPM source. KPM remains the authoritative source.

## Development roadmap

1. Phase 0: repository and Pages foundation.
2. Phase 1: discover and download public KPM calendar PDFs.
3. Phase 2: extract events into structured staging data.
4. Phase 3: validate Group A and Group B dates.
5. Phase 4: generate and test ICS feeds.
6. Phase 5: publish verified feeds.
7. Phase 6: scheduled monitoring and review workflow.

## License

Choose and add a license before accepting contributions or redistributing source documents. The project does not claim ownership of KPM publications.
