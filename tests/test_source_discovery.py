import json
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

from scripts.discover import (
    DiscoveredDocument,
    DiscoveryError,
    _deduplicate,
    discover_documents,
    identify_academic_year,
    parse_connector_config,
    parse_folder_documents,
    parse_pdf_links,
)
from scripts.download import (
    DownloadError,
    download_documents,
    is_valid_pdf,
    safe_filename,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(
        self,
        *,
        text="",
        payload=None,
        content=b"",
        status_code=200,
        headers=None,
        url="https://www.moe.gov.my/test",
    ):
        self.text = text
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self.content = content
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset:offset + chunk_size]

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses=None, error=None):
        self.responses = responses or {}
        self.error = error
        self.calls = []
        self.head_calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error:
            raise self.error
        key = urlsplit(url)._replace(query="", fragment="").geturl()
        response = self.responses.get(url) or self.responses.get(key)
        if response is None:
            raise requests.ConnectionError(f"No mocked response for {url}")
        return response

    def head(self, url, **kwargs):
        self.head_calls.append((url, kwargs))
        if self.error:
            raise self.error
        key = urlsplit(url)._replace(query="", fragment="").geturl()
        response = self.responses.get(url) or self.responses.get(key)
        if response is None:
            raise requests.ConnectionError(f"No mocked response for {url}")
        return response


def test_parse_pdf_links_and_identify_years_from_filenames_and_context():
    html = """
      <section><h2>Kalendar Akademik 2028/2029</h2>
      <a href="/files/Kalendar%20Akademik%20Tahun%202027.pdf">Muat Turun</a>
      <a href="/files/Kalendar%20Akademik%202027_2028.pdf">Muat Turun</a>
      <a href="/files/current.pdf">Muat Turun</a>
      <a href="/files/info.docx">Info</a></section>
    """
    documents = parse_pdf_links(html, "https://www.moe.gov.my/kalendar")

    assert len(documents) == 3
    assert documents[0].filename == "Kalendar Akademik Tahun 2027.pdf"
    assert documents[0].academic_year == "2027"
    assert documents[1].filename == "Kalendar Akademik 2027_2028.pdf"
    assert documents[1].academic_year == "2027-2028"
    assert documents[1].source_url == "https://www.moe.gov.my/files/Kalendar%20Akademik%202027_2028.pdf"
    assert documents[2].academic_year == "2028-2029"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Kalendar Akademik Tahun 2027.pdf", "2027"),
        ("Takwim Persekolahan 2025_2026.pdf", "2025-2026"),
        ("No year specified.pdf", None),
    ],
)
def test_identify_academic_year(value, expected):
    assert identify_academic_year(value) == expected


def test_parse_public_elfinder_config_and_calendar_folder_pdfs():
    html = """
      $('#elfinder').elfinder({
        customData: { _token: 'dynamic-token' },
        url: 'https://www.moe.gov.my/muatturun/connector'
      });
    """
    connector_url, token = parse_connector_config(html, "https://www.moe.gov.my/muatturun")
    docs = parse_folder_documents(
        {
            "files": [
                {"name": "Kalendar Akademik 2027.pdf", "mime": "application/pdf", "hash": "l1/hash"},
                {"name": "Jadual Waktu SPM 2027.pdf", "mime": "application/pdf", "hash": "l1/exam"},
                {"name": "Takwim Peperiksaan 2018.pdf", "mime": "application/pdf", "hash": "l1/exam-calendar"},
                {"name": "Takwim 2025-2026.pdf", "mime": "application/pdf", "hash": "l1/calendar"},
                {"name": "notes.txt", "mime": "text/plain", "hash": "l1/notes"},
            ]
        },
        connector_url,
        "https://www.moe.gov.my/muatturun",
    )

    assert token == "dynamic-token"
    assert [doc.filename for doc in docs] == ["Kalendar Akademik 2027.pdf", "Takwim 2025-2026.pdf"]
    assert docs[1].academic_year == "2025-2026"
    assert "_token" not in docs[0].source_url


def test_duplicate_documents_prefer_listing_url_and_merge_year():
    direct = DiscoveredDocument(None, "Kalendar 2027.pdf", "https://www.moe.gov.my/calendar.pdf")
    connector = DiscoveredDocument(
        "2027",
        "kalendar 2027.pdf",
        "https://www.moe.gov.my/muatturun/connector?cmd=file",
        "https://www.moe.gov.my/muatturun",
    )

    assert _deduplicate([connector, direct]) == [
        DiscoveredDocument("2027", "Kalendar 2027.pdf", "https://www.moe.gov.my/calendar.pdf")
    ]
    assert _deduplicate(
        [
            DiscoveredDocument("2025", "Calendar.pdf", "https://www.moe.gov.my/2025.pdf"),
            DiscoveredDocument("2026", "Calendar.pdf", "https://www.moe.gov.my/2026.pdf"),
        ]
    ) == [
        DiscoveredDocument("2025", "Calendar.pdf", "https://www.moe.gov.my/2025.pdf"),
        DiscoveredDocument("2026", "Calendar.pdf", "https://www.moe.gov.my/2026.pdf"),
    ]


def test_pdf_validation_checksum_and_safe_filename(tmp_path):
    pdf = tmp_path / "source.pdf"
    pdf.write_bytes(b"%PDF-1.7\nexample")

    assert is_valid_pdf(pdf.read_bytes())
    assert not is_valid_pdf(b"<html>not a PDF</html>")
    assert sha256_file(pdf) == "4c05a9d358d6ae170333b35b69ddf857bde90fe521d98136c23d8cda233fcedd"
    assert safe_filename("../../Kalendar 2027.pdf") == "Kalendar-2027.pdf"


@pytest.mark.parametrize(
    ("status_code", "headers", "content", "message"),
    [
        (404, {"Content-Type": "application/pdf"}, b"%PDF-1.7", "Unable to download"),
        (200, {"Content-Type": "text/html"}, b"%PDF-1.7", "Expected application/pdf"),
        (200, {"Content-Type": "application/pdf"}, b"<html>not a PDF</html>", "PDF signature"),
    ],
)
def test_download_rejects_http_or_pdf_validation_failures(
    tmp_path, status_code, headers, content, message
):
    document = DiscoveredDocument("2027", "calendar.pdf", "https://www.moe.gov.my/calendar.pdf")
    manifest = tmp_path / "source-manifest.json"
    original = {"schema_version": 1, "documents": []}
    manifest.write_text(json.dumps(original), encoding="utf-8")
    response = FakeResponse(status_code=status_code, headers=headers, content=content)

    with pytest.raises(DownloadError, match=message):
        download_documents(
            [document],
            manifest,
            tmp_path / "pdfs",
            FakeSession({document.source_url: response}),
        )

    assert response.closed
    assert json.loads(manifest.read_text(encoding="utf-8")) == original


def test_manifest_update_preserves_old_records_and_downloads_pdf(tmp_path):
    manifest_path = tmp_path / "source-manifest.json"
    old_record = {
        "academic_year": "2024",
        "filename": "old.pdf",
        "source_url": "https://www.moe.gov.my/old.pdf",
        "sha256": "old-checksum",
        "downloaded_at": "2025-01-01T00:00:00+00:00",
        "status": "downloaded",
    }
    manifest_path.write_text(
        json.dumps({"schema_version": 1, "documents": [old_record]}),
        encoding="utf-8",
    )
    document = DiscoveredDocument(
        "2027",
        "Kalendar Akademik 2027.pdf",
        "https://www.moe.gov.my/calendar.pdf",
    )
    session = FakeSession(
        {
            document.source_url: FakeResponse(
                content=b"%PDF-1.7\ncalendar",
                headers={"Content-Type": "application/pdf"},
            )
        }
    )

    result = download_documents([document], manifest_path, tmp_path / "pdfs", session)

    assert len(result["documents"]) == 2
    assert result["documents"][0] == old_record
    downloaded = result["documents"][1]
    assert downloaded["filename"] == "Kalendar-Akademik-2027.pdf"
    assert downloaded["source_url"] == document.source_url
    assert downloaded["status"] == "downloaded"
    assert downloaded["sha256"] == sha256_file(tmp_path / "pdfs" / downloaded["filename"])
    assert result["last_checked"]


def test_manifest_update_preserves_duplicate_existing_records(tmp_path):
    duplicate = {
        "academic_year": "2024",
        "filename": "old.pdf",
        "source_url": "https://www.moe.gov.my/old.pdf",
        "sha256": "old-checksum",
        "downloaded_at": "2025-01-01T00:00:00+00:00",
        "status": "downloaded",
    }
    manifest_path = tmp_path / "source-manifest.json"
    manifest_path.write_text(
        json.dumps({"schema_version": 1, "documents": [duplicate, dict(duplicate)]}),
        encoding="utf-8",
    )
    document = DiscoveredDocument("2027", "calendar.pdf", "https://www.moe.gov.my/calendar.pdf")
    session = FakeSession(
        {
            document.source_url: FakeResponse(
                content=b"%PDF-1.7\ncalendar",
                headers={"Content-Type": "application/pdf"},
            )
        }
    )

    result = download_documents([document], manifest_path, tmp_path / "pdfs", session)

    assert result["documents"][:2] == [duplicate, duplicate]
    assert len(result["documents"]) == 3


def test_duplicate_download_uses_conditional_request_and_keeps_file(tmp_path):
    download_dir = tmp_path / "pdfs"
    download_dir.mkdir()
    existing = download_dir / "calendar.pdf"
    existing.write_bytes(b"%PDF-1.7\ncurrent")
    document = DiscoveredDocument("2027", "calendar.pdf", "https://www.moe.gov.my/calendar.pdf")
    record = {
        "academic_year": "2027",
        "filename": "calendar.pdf",
        "source_url": document.source_url,
        "sha256": sha256_file(existing),
        "downloaded_at": "2026-01-01T00:00:00+00:00",
        "status": "downloaded",
        "etag": '"version-1"',
    }
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "documents": [record]}), encoding="utf-8")
    response = FakeResponse(status_code=304)
    session = FakeSession({document.source_url: response})

    result = download_documents([document], manifest, download_dir, session)

    assert result["documents"][0]["status"] == "unchanged"
    assert session.head_calls[0][1]["headers"] == {"If-None-Match": '"version-1"'}
    assert not session.calls
    assert response.closed


def test_head_metadata_skips_unchanged_pdf_without_get(tmp_path):
    download_dir = tmp_path / "pdfs"
    download_dir.mkdir()
    existing = download_dir / "calendar.pdf"
    content = b"%PDF-1.7\ncurrent"
    existing.write_bytes(content)
    document = DiscoveredDocument("2027", "calendar.pdf", "https://www.moe.gov.my/calendar.pdf")
    record = {
        "academic_year": "2027",
        "filename": "calendar.pdf",
        "source_url": document.source_url,
        "sha256": sha256_file(existing),
        "downloaded_at": "2026-01-01T00:00:00+00:00",
        "status": "downloaded",
        "last_modified": "Fri, 02 Oct 2026 14:20:01 GMT",
        "content_length": str(len(content)),
    }
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text(json.dumps({"schema_version": 1, "documents": [record]}), encoding="utf-8")
    response = FakeResponse(
        headers={
            "Content-Type": "application/pdf",
            "Last-Modified": record["last_modified"],
            "Content-Length": str(len(content)),
        }
    )
    session = FakeSession({document.source_url: response})

    result = download_documents([document], manifest, download_dir, session)

    assert result["documents"][0]["status"] == "unchanged"
    assert not session.calls
    assert response.closed


def test_network_failure_does_not_replace_manifest_or_return_success(tmp_path):
    document = DiscoveredDocument("2027", "calendar.pdf", "https://www.moe.gov.my/calendar.pdf")
    manifest = tmp_path / "source-manifest.json"
    original = {"schema_version": 1, "documents": []}
    manifest.write_text(json.dumps(original), encoding="utf-8")
    session = FakeSession(error=requests.ConnectionError("offline"))

    with pytest.raises(DownloadError, match="Some KPM PDFs could not be processed"):
        download_documents([document], manifest, tmp_path / "pdfs", session)

    assert json.loads(manifest.read_text(encoding="utf-8")) == original


def test_discovery_network_failure_is_explicit_and_does_not_return_empty(tmp_path):
    config = tmp_path / "sources.json"
    config.write_text(
        json.dumps(
            {
                "sources": [
                    {"url": "https://www.moe.gov.my/listing", "type": "public_listing", "enabled": True},
                    {
                        "url": "https://www.moe.gov.my/muatturun#elf_l1_folder",
                        "type": "public_download_folder",
                        "enabled": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(DiscoveryError, match="Unable to retrieve KPM source"):
        discover_documents(FakeSession(error=requests.ConnectionError("offline")), config)


def test_discovery_uses_public_connector_config_without_persisting_token(tmp_path):
    listing_url = "https://www.moe.gov.my/kalendar"
    folder_url = "https://www.moe.gov.my/muatturun#elf_l1_folder"
    connector_url = "https://www.moe.gov.my/muatturun/connector"
    config = tmp_path / "sources.json"
    config.write_text(
        json.dumps(
            {
                "sources": [
                    {"url": listing_url, "type": "public_listing", "enabled": True},
                    {"url": folder_url, "type": "public_download_folder", "enabled": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    session = FakeSession(
        {
            listing_url: FakeResponse(
                text='<a href="/files/Kalendar%202027.pdf">Muat Turun</a>',
                url=listing_url,
            ),
            "https://www.moe.gov.my/muatturun": FakeResponse(
                text=(
                    "$('#elfinder').elfinder({ _token: 'current-page-token', "
                    "url: 'https://www.moe.gov.my/muatturun/connector' });"
                ),
                url="https://www.moe.gov.my/muatturun",
            ),
            connector_url: FakeResponse(
                payload={
                    "files": [
                        {
                            "name": "Takwim 2025-2026.pdf",
                            "mime": "application/pdf",
                            "hash": "l1/calendar",
                        }
                    ]
                }
            ),
        }
    )

    documents = discover_documents(session, config)

    assert [document.filename for document in documents] == [
        "Kalendar 2027.pdf",
        "Takwim 2025-2026.pdf",
    ]
    assert session.calls[-1][1]["params"]["_token"] == "current-page-token"
    assert all("current-page-token" not in document.source_url for document in documents)
