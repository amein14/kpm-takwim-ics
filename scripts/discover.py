"""Discover official KPM school-calendar PDFs from public KPM sources."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "config" / "sources.json"
DEFAULT_OUTPUT = ROOT / "data" / "discovered-documents.json"
TIMEOUT = 25
USER_AGENT = "kpm-takwim-ics/1.0 (public school-calendar source checker)"
PDF_LINK = re.compile(r"\.pdf(?:$|[?#])", re.IGNORECASE)
YEAR_PAIR = re.compile(r"(?<!\d)(20\d{2})[\s_/-]+(20\d{2})(?!\d)")
YEAR = re.compile(r"(?<!\d)(20\d{2})(?!\d)")
CALENDAR_FILE = re.compile(r"kalendar|takwim|lampiran", re.IGNORECASE)


class DiscoveryError(RuntimeError):
    """Raised when an enabled KPM source cannot be queried or parsed."""


@dataclass(frozen=True)
class DiscoveredDocument:
    academic_year: str | None
    filename: str
    source_url: str
    download_page_url: str | None = None


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self._text = ""
        self._active: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self._active = {"href": href, "text": "", "start": len(self._text)}

    def handle_data(self, data: str) -> None:
        self._text += data
        if self._active is not None:
            self._active["text"] += data

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._active is not None:
            self._active["end"] = len(self._text)
            self.links.append(self._active)
            self._active = None

    def context_for(self, link: dict[str, Any], limit: int = 180) -> str:
        start = link["start"]
        end = link["end"]
        before = self._text[max(0, start - limit):start]
        after = self._text[end:end + limit]
        return f"{before} {after}"


def build_session() -> requests.Session:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _get(session: requests.Session, url: str, *, params: dict[str, str] | None = None) -> requests.Response:
    try:
        response = session.get(url, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        return response
    except requests.RequestException as exc:
        raise DiscoveryError(f"Unable to retrieve KPM source {url}: {exc}") from exc


def identify_academic_year(*values: str) -> str | None:
    for value in values:
        pair = YEAR_PAIR.search(value)
        if pair:
            return f"{pair.group(1)}-{pair.group(2)}"
        match = YEAR.search(value)
        if match:
            return match.group(1)
    return None


def parse_pdf_links(html: str, page_url: str) -> list[DiscoveredDocument]:
    parser = LinkParser()
    parser.feed(html)
    documents: list[DiscoveredDocument] = []
    for link in parser.links:
        href = link["href"].strip()
        if not PDF_LINK.search(href):
            continue
        source_url = urljoin(page_url, href)
        filename = unquote(urlsplit(source_url).path.rsplit("/", 1)[-1])
        documents.append(
            DiscoveredDocument(
                academic_year=identify_academic_year(link["text"], filename, parser.context_for(link)),
                filename=filename,
                source_url=source_url,
            )
        )
    return documents


def parse_connector_config(html: str, page_url: str) -> tuple[str, str]:
    connector = re.search(
        r"\burl\s*:\s*['\"]([^'\"]*/connector)['\"]",
        html,
        re.IGNORECASE,
    )
    token = re.search(r"\b_token\s*:\s*['\"]([^'\"]+)['\"]", html)
    if not connector or not token:
        raise DiscoveryError(
            "KPM download page no longer exposes the expected public elFinder connector configuration."
        )
    return urljoin(page_url, connector.group(1)), token.group(1)


def _folder_target(folder_url: str) -> str:
    fragment = urlsplit(folder_url).fragment
    match = re.fullmatch(r"elf_(l1_.+)", fragment)
    if not match:
        raise DiscoveryError(
            "KPM download-folder URL does not contain the expected elFinder folder reference."
        )
    return match.group(1)


def parse_folder_documents(
    payload: dict[str, Any],
    connector_url: str,
    folder_page_url: str,
) -> list[DiscoveredDocument]:
    if payload.get("error"):
        errors = payload["error"]
        raise DiscoveryError(f"KPM elFinder connector reported an error: {errors}")
    files = payload.get("files")
    if not isinstance(files, list):
        raise DiscoveryError("KPM elFinder connector response has no valid 'files' list.")

    documents: list[DiscoveredDocument] = []
    for entry in files:
        if not isinstance(entry, dict) or entry.get("mime") != "application/pdf":
            continue
        filename = str(entry.get("name", "")).strip()
        target = entry.get("hash")
        if not filename.lower().endswith(".pdf") or not isinstance(target, str):
            continue
        if not CALENDAR_FILE.search(filename) or "peperiksaan" in filename.casefold():
            continue
        source_url = (
            f"{connector_url}?cmd=file&target={quote(target, safe='')}&download=1"
        )
        documents.append(
            DiscoveredDocument(
                academic_year=identify_academic_year(filename),
                filename=filename,
                source_url=source_url,
                download_page_url=folder_page_url,
            )
        )
    return documents


def _deduplicate(documents: list[DiscoveredDocument]) -> list[DiscoveredDocument]:
    unique: list[DiscoveredDocument] = []
    for document in documents:
        key = " ".join(document.filename.casefold().split())
        duplicate_index = next(
            (
                index
                for index, previous in enumerate(unique)
                if " ".join(previous.filename.casefold().split()) == key
                and (
                    previous.academic_year == document.academic_year
                    or previous.academic_year is None
                    or document.academic_year is None
                )
            ),
            None,
        )
        if duplicate_index is None:
            unique.append(document)
            continue

        previous = unique[duplicate_index]
        if previous.download_page_url and not document.download_page_url:
            unique[duplicate_index] = DiscoveredDocument(
                academic_year=previous.academic_year or document.academic_year,
                filename=document.filename,
                source_url=document.source_url,
            )
        elif previous.academic_year is None and document.academic_year is not None:
            unique[duplicate_index] = DiscoveredDocument(
                academic_year=document.academic_year,
                filename=previous.filename,
                source_url=previous.source_url,
                download_page_url=previous.download_page_url,
            )
    return unique


def discover_documents(
    session: requests.Session | None = None,
    sources_path: Path = SOURCES_PATH,
) -> list[DiscoveredDocument]:
    """Fetch enabled public KPM sources and return deduplicated PDF documents."""
    client = session or build_session()
    try:
        source_data = json.loads(sources_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiscoveryError(f"Unable to read KPM source configuration {sources_path}: {exc}") from exc

    sources = [item for item in source_data.get("sources", []) if item.get("enabled")]
    listing = next((item for item in sources if item.get("type") == "public_listing"), None)
    folder = next((item for item in sources if item.get("type") == "public_download_folder"), None)
    if listing is None or folder is None:
        raise DiscoveryError("Source configuration must enable both the KPM listing and download folder.")

    listing_response = _get(client, listing["url"])
    documents = parse_pdf_links(listing_response.text, listing_response.url or listing["url"])

    folder_response = _get(client, folder["url"])
    connector_url, token = parse_connector_config(folder_response.text, folder_response.url or folder["url"])
    try:
        connector_response = client.get(
            connector_url,
            params={
                "cmd": "open",
                "target": _folder_target(folder["url"]),
                "init": "1",
                "_token": token,
            },
            timeout=TIMEOUT,
        )
        connector_response.raise_for_status()
        payload = connector_response.json()
    except (requests.RequestException, ValueError) as exc:
        raise DiscoveryError(
            f"Unable to query the public KPM elFinder connector ({type(exc).__name__})."
        ) from exc
    if not isinstance(payload, dict):
        raise DiscoveryError("KPM elFinder connector returned an unexpected JSON response.")
    documents.extend(parse_folder_documents(payload, connector_url, folder["url"].split("#", 1)[0]))
    return _deduplicate(documents)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="JSON path for discovered documents")
    args = parser.parse_args(argv)
    try:
        documents = discover_documents()
        if not documents:
            raise DiscoveryError("KPM sources were reachable, but no relevant PDF documents were found.")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps([asdict(document) for document in documents], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except (DiscoveryError, OSError) as exc:
        print(f"Discovery failed: {exc}", file=sys.stderr)
        return 1
    print(f"Discovered {len(documents)} KPM PDF document(s); results saved to {args.output}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
