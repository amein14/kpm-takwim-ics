"""Download discovered KPM PDFs and update the source manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from scripts.discover import (
    DEFAULT_OUTPUT,
    ROOT,
    TIMEOUT,
    DiscoveredDocument,
    DiscoveryError,
    build_session,
    parse_connector_config,
)

DEFAULT_MANIFEST = ROOT / "data" / "source-manifest.json"
DEFAULT_DOWNLOAD_DIR = ROOT / "data" / "source-pdfs"


class DownloadError(RuntimeError):
    """Raised when a discovered PDF cannot be safely downloaded or validated."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_valid_pdf(content: bytes) -> bool:
    return b"%PDF-" in content[:1024]


def safe_filename(filename: str) -> str:
    normalized = unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    stem = Path(normalized).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-_")
    if not stem:
        stem = "kpm-calendar"
    return f"{stem[:150]}.pdf"


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": 1, "documents": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DownloadError(f"Unable to read source manifest {path}: {exc}") from exc
    if data.get("schema_version") != 1 or not isinstance(data.get("documents"), list):
        raise DownloadError(f"Source manifest {path} has an unsupported schema.")
    return data


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
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
            json.dump(manifest, file, ensure_ascii=False, indent=2)
            file.write("\n")
            temporary_path = Path(file.name)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _request_url(source_url: str, token: str | None = None) -> str:
    parts = urlsplit(source_url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "_token"]
    if token is not None:
        query.append(("_token", token))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _existing_file_is_valid(record: dict[str, Any], download_dir: Path) -> bool:
    filename = record.get("filename")
    expected_hash = record.get("sha256")
    if not isinstance(filename, str) or not isinstance(expected_hash, str):
        return False
    path = download_dir / filename
    return path.is_file() and sha256_file(path) == expected_hash


def _remote_metadata_matches(record: dict[str, Any], response: requests.Response) -> bool:
    if response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/pdf":
        return False
    etag = record.get("etag")
    if etag:
        return response.headers.get("ETag") == etag
    last_modified = record.get("last_modified")
    content_length = record.get("content_length")
    return (
        bool(last_modified)
        and response.headers.get("Last-Modified") == last_modified
        and content_length is not None
        and response.headers.get("Content-Length") == str(content_length)
    )


def _download_one(
    document: DiscoveredDocument,
    record: dict[str, Any] | None,
    download_dir: Path,
    session: requests.Session,
    connector_tokens: dict[str, str],
) -> dict[str, Any]:
    token: str | None = None
    if document.download_page_url:
        if document.download_page_url not in connector_tokens:
            try:
                page = session.get(document.download_page_url, timeout=TIMEOUT)
                page.raise_for_status()
            except requests.RequestException as exc:
                raise DownloadError(f"Unable to retrieve KPM download page: {exc}") from exc
            _, token = parse_connector_config(page.text, page.url or document.download_page_url)
            connector_tokens[document.download_page_url] = token
        token = connector_tokens[document.download_page_url]

    headers: dict[str, str] = {}
    if record:
        if record.get("etag"):
            headers["If-None-Match"] = record["etag"]
        if record.get("last_modified"):
            headers["If-Modified-Since"] = record["last_modified"]
    request_url = _request_url(document.source_url, token)
    if record and record.get("sha256") and _existing_file_is_valid(record, download_dir):
        try:
            head_response = session.head(
                request_url,
                headers=headers,
                timeout=TIMEOUT,
                allow_redirects=True,
            )
        except requests.RequestException:
            head_response = None
        if head_response is not None:
            try:
                if head_response.status_code == 304 or (
                    head_response.status_code == 200 and _remote_metadata_matches(record, head_response)
                ):
                    unchanged = dict(record)
                    unchanged["status"] = "unchanged"
                    return unchanged
            finally:
                head_response.close()
    try:
        response = session.get(request_url, headers=headers, timeout=TIMEOUT, stream=True)
    except requests.RequestException as exc:
        detail = type(exc).__name__ if token is not None else str(exc)
        raise DownloadError(f"Unable to download {document.source_url}: {detail}") from exc
    if response.status_code == 304:
        response.close()
        if record and _existing_file_is_valid(record, download_dir):
            unchanged = dict(record)
            unchanged["status"] = "unchanged"
            return unchanged
        raise DownloadError(f"KPM returned not-modified for {document.source_url}, but no matching local PDF exists.")
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        response.close()
        detail = type(exc).__name__ if token is not None else str(exc)
        raise DownloadError(f"Unable to download {document.source_url}: {detail}") from exc

    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/pdf":
        response.close()
        raise DownloadError(
            f"Expected application/pdf from {document.source_url}, received {content_type or 'no content type'}."
        )

    filename = safe_filename(document.filename)
    destination = download_dir / filename
    download_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    header = bytearray()
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=download_dir, prefix=".download-", suffix=".tmp", delete=False) as file:
            temporary_path = Path(file.name)
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                if len(header) < 1024:
                    header.extend(chunk[:1024 - len(header)])
                digest.update(chunk)
                file.write(chunk)
        if not is_valid_pdf(bytes(header)):
            raise DownloadError(f"Downloaded content from {document.source_url} does not have a PDF signature.")

        checksum = digest.hexdigest()
        if (
            record
            and record.get("sha256") == checksum
            and _existing_file_is_valid(record, download_dir)
        ):
            temporary_path.unlink()
            temporary_path = None
            result = dict(record)
            result.update(_response_metadata(response))
            result["status"] = "unchanged"
            return result

        if destination.exists() and (record is None or record.get("filename") != filename):
            suffix = checksum[:10]
            destination = destination.with_name(f"{destination.stem}-{suffix}.pdf")
            filename = destination.name
        temporary_path.replace(destination)
        temporary_path = None
    except requests.RequestException as exc:
        detail = type(exc).__name__ if token is not None else str(exc)
        raise DownloadError(
            f"Connection interrupted while downloading {document.source_url}: {detail}"
        ) from exc
    except OSError as exc:
        raise DownloadError(f"Unable to save PDF {destination}: {exc}") from exc
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
        response.close()

    result = {
        "academic_year": document.academic_year,
        "filename": filename,
        "source_url": document.source_url,
        "sha256": checksum,
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "status": "downloaded",
    }
    result.update(_response_metadata(response))
    return result


def _response_metadata(response: requests.Response) -> dict[str, str]:
    metadata: dict[str, str] = {}
    etag = response.headers.get("ETag")
    last_modified = response.headers.get("Last-Modified")
    if etag:
        metadata["etag"] = etag
    if last_modified:
        metadata["last_modified"] = last_modified
    content_length = response.headers.get("Content-Length")
    if content_length:
        metadata["content_length"] = content_length
    return metadata


def download_documents(
    documents: list[DiscoveredDocument],
    manifest_path: Path = DEFAULT_MANIFEST,
    download_dir: Path = DEFAULT_DOWNLOAD_DIR,
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Download PDFs, preserve prior records, and atomically update the manifest."""
    if not documents:
        raise DownloadError("No discovered documents were provided; the existing manifest was left unchanged.")
    manifest = _read_manifest(manifest_path)
    original_records = list(manifest["documents"])
    updated_records = list(original_records)
    record_indexes: dict[str, int] = {}
    for index, record in enumerate(updated_records):
        if isinstance(record, dict) and isinstance(record.get("source_url"), str):
            record_indexes[record["source_url"]] = index
    client = session or build_session()
    connector_tokens: dict[str, str] = {}
    errors: list[str] = []
    changed = False

    for document in documents:
        index = record_indexes.get(document.source_url)
        previous = updated_records[index] if index is not None else None
        try:
            result = _download_one(
                document, previous, download_dir, client, connector_tokens
            )
            if index is None:
                record_indexes[document.source_url] = len(updated_records)
                updated_records.append(result)
                changed = True
            else:
                updated_records[index] = result
                changed = changed or result != previous
        except (DownloadError, DiscoveryError) as exc:
            errors.append(f"{document.filename}: {exc}")

    if changed:
        manifest["documents"] = updated_records
        manifest["schema_version"] = 1
        manifest["last_checked"] = datetime.now(timezone.utc).isoformat()
        _write_manifest(manifest_path, manifest)
    elif not errors:
        manifest["last_checked"] = datetime.now(timezone.utc).isoformat()
        _write_manifest(manifest_path, manifest)
    if errors:
        raise DownloadError("Some KPM PDFs could not be processed: " + " | ".join(errors))
    return manifest


def _load_documents(path: Path) -> list[DiscoveredDocument]:
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(items, list):
            raise ValueError("expected a JSON list")
        return [DiscoveredDocument(**item) for item in items]
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DownloadError(f"Unable to read discovered documents from {path}: {exc}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_OUTPUT, help="JSON output from scripts.discover")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOAD_DIR)
    args = parser.parse_args(argv)
    try:
        documents = _load_documents(args.input)
        manifest = download_documents(documents, args.manifest, args.download_dir)
    except (DownloadError, DiscoveryError) as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1
    print(f"Processed {len(documents)} PDF document(s); manifest contains {len(manifest['documents'])} record(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
