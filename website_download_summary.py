#!/usr/bin/env python3

from __future__ import annotations

import argparse
import base64
import binascii
import concurrent.futures
import csv
import json
import mimetypes
import posixpath
import re
import ssl
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable
from urllib import error, parse, request, robotparser


BOT_NAME = "DownloadSummaryBot"
DEFAULT_USER_AGENT = f"Mozilla/5.0 (compatible; {BOT_NAME}/1.1; +https://localhost)"
DEFAULT_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

DOWNLOAD_EXTENSIONS = {
    ".7z",
    ".apk",
    ".avi",
    ".bin",
    ".bz2",
    ".csv",
    ".deb",
    ".dmg",
    ".doc",
    ".docx",
    ".epub",
    ".exe",
    ".flac",
    ".gz",
    ".img",
    ".ipa",
    ".iso",
    ".jar",
    ".jpeg",
    ".jpg",
    ".json",
    ".m4a",
    ".mkv",
    ".mov",
    ".mp3",
    ".mp4",
    ".msi",
    ".odt",
    ".pdf",
    ".pkg",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".rtf",
    ".svg",
    ".tar",
    ".tgz",
    ".torrent",
    ".txt",
    ".wav",
    ".webm",
    ".xls",
    ".xlsx",
    ".xml",
    ".zip",
}

HTML_LIKE_EXTENSIONS = {
    "",
    ".asp",
    ".aspx",
    ".cfm",
    ".cgi",
    ".htm",
    ".html",
    ".jsp",
    ".php",
    ".shtml",
}

DOWNLOAD_HINT_KEYS = {
    "attachment",
    "dl",
    "download",
    "export",
    "file",
    "filename",
    "files",
    "format",
}

DOWNLOAD_HINT_WORDS = {
    "apk",
    "archive",
    "brochure",
    "csv",
    "download",
    "ebook",
    "export",
    "guide",
    "installer",
    "manual",
    "pdf",
    "pkg",
    "presentation",
    "report",
    "spec",
    "spreadsheet",
    "whitepaper",
    "xls",
    "xlsx",
    "zip",
}

NON_DOWNLOAD_CONTENT_TYPES = (
    "application/xhtml+xml",
    "text/html",
    "text/plain",
)

DOWNLOADABLE_CONTENT_TYPE_PREFIXES = (
    "application/",
    "audio/",
    "font/",
    "image/",
    "video/",
)

DOWNLOADABLE_CONTENT_TYPES = {
    "text/calendar",
    "text/csv",
    "text/markdown",
    "text/tab-separated-values",
    "text/xml",
}


@dataclass(frozen=True)
class CrawlLink:
    url: str
    source_page: str
    reason: str
    anchor_text: str
    method: str = "GET"
    request_data: tuple[tuple[str, str], ...] = ()
    inline_size_bytes: int | None = None
    inline_content_type: str | None = None
    filename: str | None = None


@dataclass
class DownloadRecord:
    url: str
    final_url: str
    source_page: str
    reason: str
    anchor_text: str
    method: str
    request_data: tuple[tuple[str, str], ...]
    status_code: int | None
    content_type: str | None
    size_bytes: int | None
    filename: str | None
    error_message: str | None = None


@dataclass
class CrawlStats:
    pages_scanned: int = 0
    pages_blocked_by_robots: int = 0
    crawl_delay_seconds: float = 0.0
    robots_enabled: bool = False
    robots_url: str | None = None


@dataclass
class RequestSettings:
    timeout: float
    retries: int
    backoff: float
    throttle: "HostThrottle | None" = None
    ssl_context: ssl.SSLContext | None = None
    crawl_workers: int = 1


class AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str | bool]] = []
        self.forms: list[dict[str, object]] = []
        self._active_link: dict[str, str | bool] | None = None
        self._active_form: dict[str, object] | None = None
        self._capture_form_text = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag_name = tag.lower()
        attr_map = {name.lower(): value for name, value in attrs}
        if tag_name == "a":
            href = attr_map.get("href")
            if not href:
                return
            seed_text = " ".join(
                value.strip()
                for key in ("title", "aria-label", "download")
                if (value := attr_map.get(key))
            )
            self._active_link = {
                "href": href,
                "has_download_attr": "download" in attr_map,
                "text": seed_text,
            }
            return

        if tag_name == "form":
            action = attr_map.get("action") or ""
            method = (attr_map.get("method") or "GET").upper()
            self._active_form = {
                "action": action,
                "method": method,
                "button_text": "",
                "inputs": [],
            }
            return

        if self._active_form is None:
            return

        if tag_name == "input":
            name = attr_map.get("name")
            if not name:
                return
            self._active_form["inputs"].append(
                {
                    "name": name,
                    "value": attr_map.get("value") or "",
                    "type": (attr_map.get("type") or "text").lower(),
                    "disabled": "disabled" in attr_map,
                }
            )
            input_type = (attr_map.get("type") or "").lower()
            if input_type in {"submit", "button"} and attr_map.get("value"):
                button_text = str(self._active_form.get("button_text", ""))
                self._active_form["button_text"] = normalize_space(
                    f"{button_text} {attr_map.get('value') or ''}"
                )
            return

        if tag_name == "button":
            self._capture_form_text = True
            button_seed = " ".join(
                value.strip()
                for key in ("title", "aria-label")
                if (value := attr_map.get(key))
            )
            if button_seed:
                button_text = str(self._active_form.get("button_text", ""))
                self._active_form["button_text"] = normalize_space(
                    f"{button_text} {button_seed}"
                )

    def handle_data(self, data: str) -> None:
        if self._active_link is None:
            pass
        else:
            text = str(self._active_link.get("text", ""))
            self._active_link["text"] = f"{text} {data}".strip()
        if self._active_form is not None and self._capture_form_text:
            button_text = str(self._active_form.get("button_text", ""))
            self._active_form["button_text"] = normalize_space(f"{button_text} {data}")

    def handle_endtag(self, tag: str) -> None:
        tag_name = tag.lower()
        if tag_name == "a" and self._active_link is not None:
            self.links.append(self._active_link)
            self._active_link = None
            return
        if tag_name == "button":
            self._capture_form_text = False
            return
        if tag_name == "form" and self._active_form is not None:
            self.forms.append(self._active_form)
            self._active_form = None
            self._capture_form_text = False


class HostThrottle:
    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = max(interval_seconds, 0.0)
        self._lock = threading.Lock()
        self._next_allowed_by_host: dict[str, float] = {}

    def wait(self, url: str) -> None:
        if self.interval_seconds <= 0:
            return
        host = parse.urlparse(url).netloc.lower()
        if not host:
            return
        with self._lock:
            now = time.monotonic()
            next_allowed = self._next_allowed_by_host.get(host, now)
            sleep_for = max(0.0, next_allowed - now)
            scheduled = max(now, next_allowed) + self.interval_seconds
            self._next_allowed_by_host[host] = scheduled
        if sleep_for > 0:
            time.sleep(sleep_for)


def normalize_space(value: str) -> str:
    return " ".join(value.split())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Crawl a website, find likely downloadable files, and summarize total size."
        )
    )
    parser.add_argument("url", help="Starting page URL, for example https://example.com")
    parser.add_argument(
        "--max-pages",
        type=int,
        default=25,
        help="Maximum number of HTML pages to crawl on the same site. Default: 25",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel workers for file-size checks. Default: 8",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="Network timeout in seconds. Default: 15",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=2,
        help="Retries for transient request failures. Default: 2",
    )
    parser.add_argument(
        "--backoff",
        type=float,
        default=1.0,
        help="Base backoff in seconds between retries. Default: 1",
    )
    parser.add_argument(
        "--allow-subdomains",
        action="store_true",
        help="Include links from subdomains of the starting host.",
    )
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="Ignore robots.txt rules and crawl-delay guidance.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS certificate verification for sites with broken cert chains.",
    )
    parser.add_argument(
        "--show-links",
        action="store_true",
        help="Print every discovered download link and its size.",
    )
    parser.add_argument(
        "--json-out",
        help="Write summary and discovered records to a JSON file.",
    )
    parser.add_argument(
        "--csv-out",
        help="Write discovered records to a CSV file.",
    )
    return parser.parse_args()


def normalize_url(base_url: str, href: str) -> str | None:
    joined = parse.urljoin(base_url, href)
    parsed = parse.urlparse(joined)
    if parsed.scheme not in {"http", "https"}:
        return None
    cleaned = parsed._replace(fragment="")
    return parse.urlunparse(cleaned)


def host_matches(target_host: str, root_host: str, allow_subdomains: bool) -> bool:
    if target_host == root_host:
        return True
    return allow_subdomains and target_host.endswith("." + root_host)


def file_extension(url: str) -> str:
    path = parse.urlparse(url).path
    return posixpath.splitext(path)[1].lower()


def extension_from_value(value: str) -> str:
    decoded = parse.unquote(value.lower())
    return posixpath.splitext(decoded)[1]


def text_contains_download_hint(text: str) -> bool:
    lowered = normalize_space(text).lower()
    if not lowered:
        return False
    if any(word in lowered for word in DOWNLOAD_HINT_WORDS):
        return True
    return any(ext in lowered for ext in DOWNLOAD_EXTENSIONS)


def parse_human_size_to_bytes(value: str) -> int | None:
    match = re.search(r"(\d+(?:\.\d+)?)\s*([KMGT]?B)", value.strip(), re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).upper()
    scale = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }.get(unit)
    if scale is None:
        return None
    return int(number * scale)


def decode_base64_text(value: str) -> str | None:
    try:
        decoded = base64.b64decode(value).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
    decoded = normalize_space(decoded)
    return decoded or None


def infer_content_type(url: str, filename: str | None) -> str | None:
    probe = filename or parse.urlparse(url).path
    if not probe:
        return None
    guessed, _ = mimetypes.guess_type(probe)
    return guessed


def same_site_or_subdomain(target_host: str, root_host: str) -> bool:
    if not target_host or not root_host:
        return False
    return target_host == root_host or target_host.endswith("." + root_host)


def crawl_scope_prefix(start_url: str) -> str | None:
    path = parse.urlparse(start_url).path or "/"
    if path in {"", "/"}:
        return None
    trimmed = path.rstrip("/")
    segments = [segment for segment in trimmed.split("/") if segment]
    if not segments:
        return None
    last = segments[-1]
    if re.fullmatch(r"\d+", last) or "." in last:
        segments = segments[:-1]
    if not segments:
        return None
    return "/" + "/".join(segments)


def page_is_in_scope(url: str, scope_prefix: str | None) -> bool:
    if scope_prefix is None:
        return True
    path = parse.urlparse(url).path.rstrip("/") or "/"
    # Vimm section pages link to game-detail pages under /vault/<numeric-id>.
    if re.fullmatch(r"/vault/\d+", path):
        return True
    return path == scope_prefix or path.startswith(scope_prefix + "/")


def crawl_priority(url: str) -> int:
    parsed = parse.urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    query = parsed.query.lower()
    if re.fullmatch(r"/vault/\d+", path):
        return 0
    if "p=list" in query:
        return 1
    if re.fullmatch(r"/vault/[^/]+/[a-z0-9#]", path, re.IGNORECASE):
        return 1
    if re.fullmatch(r"/vault/[^/]+", path):
        return 2
    return 3


def enqueue_page(
    queue: deque[tuple[str, int]],
    queued: set[str],
    url: str,
    depth: int,
) -> None:
    if url in queued:
        return
    queued.add(url)
    if crawl_priority(url) <= 1:
        queue.appendleft((url, depth))
    else:
        queue.append((url, depth))


def looks_like_download_form(
    action_url: str,
    method: str,
    button_text: str,
    inputs: list[dict[str, object]],
) -> tuple[bool, str]:
    normalized_text = normalize_space(button_text)
    if text_contains_download_hint(normalized_text):
        return True, "download form"

    input_names = {
        str(item.get("name", "")).lower()
        for item in inputs
        if not bool(item.get("disabled"))
    }
    if {"mediaid", "file", "download"} & input_names:
        return True, "download form input"

    parsed = parse.urlparse(action_url)
    host_and_path = f"{parsed.netloc}{parsed.path}".lower()
    if "download" in host_and_path or parsed.netloc.lower().startswith("dl"):
        return True, "download form action"

    return False, ""


def extract_media_catalog(html: str) -> dict[str, dict[str, object]]:
    match = re.search(r"\bconst\s+media\s*=\s*(\[[\s\S]*?\]);", html)
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}

    catalog: dict[str, dict[str, object]] = {}
    for item in payload:
        media_id = str(item.get("ID", "")).strip()
        if not media_id:
            continue
        sizes = {
            "0": parse_human_size_to_bytes(str(item.get("ZippedText", ""))),
            "1": parse_human_size_to_bytes(str(item.get("AltZippedText", ""))),
            "2": parse_human_size_to_bytes(str(item.get("AltZipped2Text", ""))),
        }
        catalog[media_id] = {
            "filename": decode_base64_text(str(item.get("GoodTitle", "") or "")),
            "size_by_alt": sizes,
        }
    return catalog


def resolve_media_details(
    media_catalog: dict[str, dict[str, object]],
    request_data: tuple[tuple[str, str], ...],
    action_url: str,
) -> tuple[int | None, str | None, str | None]:
    form_data = {key: value for key, value in request_data}
    media_id = form_data.get("mediaId")
    alt = form_data.get("alt", "0")
    if not media_id:
        return None, None, None
    entry = media_catalog.get(media_id)
    if not entry:
        return None, None, None
    size_map = entry.get("size_by_alt", {})
    size_bytes = None
    if isinstance(size_map, dict):
        candidate = size_map.get(alt)
        if isinstance(candidate, int):
            size_bytes = candidate
        elif alt != "0":
            fallback = size_map.get("0")
            if isinstance(fallback, int):
                size_bytes = fallback
    filename = entry.get("filename")
    filename_text = filename if isinstance(filename, str) else None
    return size_bytes, infer_content_type(action_url, filename_text), filename_text


def looks_like_download_url(
    url: str,
    anchor_text: str = "",
    has_download_attr: bool = False,
) -> tuple[bool, str]:
    if has_download_attr:
        return True, "download attribute"

    parsed = parse.urlparse(url)
    extension = file_extension(url)
    if extension in DOWNLOAD_EXTENSIONS:
        return True, f"file extension {extension}"

    query = parse.parse_qs(parsed.query)
    query_keys = {key.lower() for key in query}
    if query_keys & DOWNLOAD_HINT_KEYS:
        return True, "download-like query parameter"
    for values in query.values():
        for value in values:
            lowered = value.lower()
            if any(hint in lowered for hint in DOWNLOAD_HINT_KEYS):
                return True, "download-like query value"
            value_extension = extension_from_value(lowered)
            if value_extension in DOWNLOAD_EXTENSIONS:
                return True, f"query suggests {value_extension}"

    path_lower = parse.unquote(parsed.path.lower())
    if any(f"/{hint}/" in path_lower for hint in ("download", "downloads", "files")):
        return True, "download-like path"

    if text_contains_download_hint(anchor_text):
        return True, "download-like anchor text"

    return False, ""


def looks_like_html_page(url: str) -> bool:
    ext = file_extension(url)
    if ext in HTML_LIKE_EXTENSIONS:
        return True
    return ext not in DOWNLOAD_EXTENSIONS


def sleep_before_retry(attempt: int, backoff: float) -> None:
    delay = max(backoff, 0.0) * (2**attempt)
    if delay > 0:
        time.sleep(delay)


def make_request(
    url: str,
    settings: RequestSettings,
    method: str = "GET",
    extra_headers: dict[str, str] | None = None,
):
    headers = {"User-Agent": DEFAULT_USER_AGENT}
    if extra_headers:
        headers.update(extra_headers)
    retries = max(settings.retries, 0)
    for attempt in range(retries + 1):
        if settings.throttle is not None:
            settings.throttle.wait(url)
        req = request.Request(url, headers=headers, method=method)
        try:
            return request.urlopen(
                req,
                timeout=settings.timeout,
                context=settings.ssl_context,
            )
        except error.HTTPError as exc:
            if exc.code in DEFAULT_RETRYABLE_STATUS_CODES and attempt < retries:
                sleep_before_retry(attempt, settings.backoff)
                continue
            raise
        except (error.URLError, TimeoutError, OSError):
            if attempt < retries:
                sleep_before_retry(attempt, settings.backoff)
                continue
            raise


def fetch_html(url: str, settings: RequestSettings) -> tuple[str, str] | None:
    try:
        with make_request(url, settings=settings, method="GET") as response:
            content_type = response.headers.get_content_type().lower()
            if "html" not in content_type:
                return None
            charset = response.headers.get_content_charset() or "utf-8"
            body = response.read().decode(charset, errors="replace")
            return response.geturl(), body
    except (error.HTTPError, error.URLError, TimeoutError, ValueError, OSError):
        return None


def build_robots_parser(start_url: str, settings: RequestSettings) -> tuple[robotparser.RobotFileParser | None, str]:
    parsed = parse.urlparse(start_url)
    robots_url = parse.urlunparse((parsed.scheme, parsed.netloc, "/robots.txt", "", "", ""))
    parser_obj = robotparser.RobotFileParser()
    try:
        with make_request(robots_url, settings=settings, method="GET") as response:
            charset = response.headers.get_content_charset() or "utf-8"
            content = response.read().decode(charset, errors="replace")
    except (error.HTTPError, error.URLError, TimeoutError, ValueError, OSError):
        return None, robots_url
    parser_obj.set_url(robots_url)
    parser_obj.parse(content.splitlines())
    return parser_obj, robots_url


def can_fetch_url(parser_obj: robotparser.RobotFileParser | None, url: str) -> bool:
    if parser_obj is None:
        return True
    return parser_obj.can_fetch(BOT_NAME, url)


def robots_crawl_delay(parser_obj: robotparser.RobotFileParser | None) -> float:
    if parser_obj is None:
        return 0.0
    delay = parser_obj.crawl_delay(BOT_NAME)
    if delay is None:
        delay = parser_obj.crawl_delay("*")
    return float(delay or 0.0)


def crawl_site(
    start_url: str,
    max_pages: int,
    allow_subdomains: bool,
    settings: RequestSettings,
    ignore_robots: bool,
    max_depth: int | None = None,
    on_candidate: Callable[[CrawlLink, CrawlStats], None] | None = None,
) -> tuple[list[CrawlLink], CrawlStats]:
    root = parse.urlparse(start_url)
    if root.scheme not in {"http", "https"} or not root.netloc:
        raise ValueError("URL must include http:// or https:// and a valid hostname")

    stats = CrawlStats()
    robots_parser = None
    if not ignore_robots:
        robots_parser, robots_url = build_robots_parser(start_url, settings)
        stats.robots_enabled = robots_parser is not None
        stats.robots_url = robots_url
        crawl_delay = robots_crawl_delay(robots_parser)
        if crawl_delay > 0:
            settings.throttle = HostThrottle(crawl_delay)
            stats.crawl_delay_seconds = crawl_delay

    root_host = root.hostname or ""
    scope_prefix = crawl_scope_prefix(start_url)
    queue = deque([(start_url, 0)])
    queued = {start_url}
    visited_pages: set[str] = set()
    download_candidates: dict[str, CrawlLink] = {}

    crawl_workers = max(settings.crawl_workers, 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=crawl_workers) as executor:
        while queue and len(visited_pages) < max_pages:
            batch: list[tuple[str, int]] = []
            while queue and len(batch) < crawl_workers and len(visited_pages) + len(batch) < max_pages:
                current = queue.popleft()
                if current in visited_pages:
                    continue
                current_url, current_depth = current
                if robots_parser is not None and not can_fetch_url(robots_parser, current_url):
                    stats.pages_blocked_by_robots += 1
                    continue
                visited_pages.add(current_url)
                batch.append(current)

            if not batch:
                continue

            future_map = {
                executor.submit(fetch_html, current_url, settings): (current_url, current_depth)
                for current_url, current_depth in batch
            }

            for future in concurrent.futures.as_completed(future_map):
                current_url, current_depth = future_map[future]
                html_result = future.result()
                if not html_result:
                    continue

                final_url, html = html_result
                stats.pages_scanned += 1
                parser_obj = AnchorParser()
                parser_obj.feed(html)
                media_catalog = extract_media_catalog(html)

                for item in parser_obj.links:
                    href = str(item["href"])
                    has_download_attr = bool(item["has_download_attr"])
                    anchor_text = normalize_space(str(item.get("text", "")))
                    normalized = normalize_url(final_url, href)
                    if not normalized:
                        continue
                    parsed = parse.urlparse(normalized)
                    target_host = parsed.hostname or ""
                    if not host_matches(target_host, root_host, allow_subdomains):
                        continue
                    if not page_is_in_scope(normalized, scope_prefix):
                        continue

                    matches_download, reason = looks_like_download_url(
                        normalized,
                        anchor_text=anchor_text,
                        has_download_attr=has_download_attr,
                    )
                    if matches_download:
                        if normalized not in download_candidates:
                            candidate = CrawlLink(
                                url=normalized,
                                source_page=final_url,
                                reason=reason,
                                anchor_text=anchor_text,
                            )
                            download_candidates[normalized] = candidate
                            if on_candidate is not None:
                                on_candidate(candidate, stats)
                        continue

                    if looks_like_html_page(normalized) and (
                        max_depth is None or current_depth < max_depth
                    ):
                        enqueue_page(queue, queued, normalized, current_depth + 1)

                for form in parser_obj.forms:
                    action_raw = normalize_space(str(form.get("action", "")))
                    action_target = action_raw or final_url
                    normalized_action = normalize_url(final_url, action_target)
                    if not normalized_action:
                        continue
                    parsed_action = parse.urlparse(normalized_action)
                    target_host = parsed_action.hostname or ""
                    if not same_site_or_subdomain(target_host, root_host):
                        continue
                    inputs = [
                        item
                        for item in form.get("inputs", [])
                        if isinstance(item, dict)
                    ]
                    button_text = normalize_space(str(form.get("button_text", "")))
                    method = normalize_space(str(form.get("method", "GET")).upper()) or "GET"
                    matches_download, reason = looks_like_download_form(
                        normalized_action,
                        method,
                        button_text,
                        inputs,
                    )
                    if not matches_download:
                        continue
                    request_data = tuple(
                        sorted(
                            (
                                str(item.get("name", "")),
                                str(item.get("value", "")),
                            )
                            for item in inputs
                            if item.get("name") and not bool(item.get("disabled"))
                        )
                    )
                    inline_size, inline_content_type, filename = resolve_media_details(
                        media_catalog,
                        request_data,
                        normalized_action,
                    )
                    form_key = (normalized_action, method, request_data)
                    existing = download_candidates.get(str(form_key))
                    if existing is None:
                        candidate = CrawlLink(
                            url=normalized_action,
                            source_page=final_url,
                            reason=reason,
                            anchor_text=button_text,
                            method=method,
                            request_data=request_data,
                            inline_size_bytes=inline_size,
                            inline_content_type=inline_content_type,
                            filename=filename,
                        )
                        download_candidates[str(form_key)] = candidate
                        if on_candidate is not None:
                            on_candidate(candidate, stats)

    return list(download_candidates.values()), stats


def run_discovery(
    *,
    start_url: str,
    max_pages: int,
    allow_subdomains: bool,
    ignore_robots: bool,
    settings: RequestSettings,
    inspect_workers: int | None = None,
    max_depth: int | None = None,
    on_candidate: Callable[[CrawlLink, CrawlStats], None] | None = None,
    on_record: Callable[[CrawlLink, DownloadRecord | None, CrawlStats, int, int], None] | None = None,
) -> tuple[dict[str, object], list[DownloadRecord], CrawlStats]:
    candidates, stats = crawl_site(
        start_url=start_url,
        max_pages=max_pages,
        allow_subdomains=allow_subdomains,
        settings=settings,
        ignore_robots=ignore_robots,
        max_depth=max_depth,
        on_candidate=on_candidate,
    )

    if not candidates:
        return build_summary(start_url, stats, []), [], stats

    records: list[DownloadRecord] = []
    inspect_pool = max(inspect_workers or settings.crawl_workers, 1)
    with concurrent.futures.ThreadPoolExecutor(max_workers=inspect_pool) as executor:
        future_map = {
            executor.submit(inspect_download, candidate, settings): candidate
            for candidate in candidates
        }
        completed = 0
        total = len(candidates)
        for future in concurrent.futures.as_completed(future_map):
            candidate = future_map[future]
            record = future.result()
            completed += 1
            if on_record is not None:
                on_record(candidate, record, stats, completed, total)
            if record is not None:
                records.append(record)

    records = dedupe_records(records)
    records.sort(key=lambda item: (item.size_bytes is None, -(item.size_bytes or 0), item.final_url))
    summary = build_summary(start_url, stats, records)
    return summary, records, stats


def parse_content_length(headers) -> int | None:
    raw = headers.get("Content-Length")
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def parse_content_range_total(headers) -> int | None:
    raw = headers.get("Content-Range")
    if not raw or "/" not in raw:
        return None
    total = raw.rsplit("/", 1)[-1].strip()
    if total == "*":
        return None
    try:
        value = int(total)
    except ValueError:
        return None
    return value if value >= 0 else None


def extract_filename(final_url: str, headers) -> str | None:
    disposition = headers.get("Content-Disposition", "")
    filename_star = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", disposition, re.IGNORECASE)
    if filename_star:
        return parse.unquote(filename_star.group(1).strip().strip("\"'"))
    filename_match = re.search(r'filename\s*=\s*"([^"]+)"', disposition, re.IGNORECASE)
    if filename_match:
        return filename_match.group(1).strip()
    filename_match = re.search(r"filename\s*=\s*([^;]+)", disposition, re.IGNORECASE)
    if filename_match:
        return filename_match.group(1).strip().strip("\"'")
    path_name = posixpath.basename(parse.urlparse(final_url).path)
    return path_name or None


def is_downloadable_content_type(content_type: str) -> bool:
    lowered = content_type.lower()
    if lowered in NON_DOWNLOAD_CONTENT_TYPES:
        return False
    if lowered in DOWNLOADABLE_CONTENT_TYPES:
        return True
    return lowered.startswith(DOWNLOADABLE_CONTENT_TYPE_PREFIXES)


def is_probably_download(url: str, headers, reason: str, anchor_text: str) -> bool:
    disposition = headers.get("Content-Disposition", "").lower()
    if "attachment" in disposition:
        return True
    content_type = headers.get_content_type().lower()
    if content_type in NON_DOWNLOAD_CONTENT_TYPES:
        return False
    if content_type.startswith("text/html"):
        return False
    if reason == "download attribute" or reason.startswith("file extension "):
        return True
    if is_downloadable_content_type(content_type):
        return True
    if text_contains_download_hint(anchor_text):
        return True
    ext = file_extension(url)
    if ext in DOWNLOAD_EXTENSIONS:
        return True
    return False


def status_supports_record(status_code: int | None) -> bool:
    if status_code is None:
        return True
    if 200 <= status_code < 400:
        return True
    return status_code in {401, 403}


def inspect_download(link: CrawlLink, settings: RequestSettings) -> DownloadRecord | None:
    if link.method != "GET" or link.request_data or link.inline_size_bytes is not None:
        return DownloadRecord(
            url=link.url,
            final_url=link.url,
            source_page=link.source_page,
            reason=link.reason,
            anchor_text=link.anchor_text,
            method=link.method,
            request_data=link.request_data,
            status_code=None,
            content_type=link.inline_content_type,
            size_bytes=link.inline_size_bytes,
            filename=link.filename,
            error_message=None,
        )

    head_error: str | None = None
    head_headers = None
    final_url = link.url
    status_code: int | None = None

    try:
        with make_request(link.url, settings=settings, method="HEAD") as response:
            head_headers = response.headers
            final_url = response.geturl()
            status_code = getattr(response, "status", None)
    except error.HTTPError as exc:
        head_error = f"HEAD failed with HTTP {exc.code}"
        if exc.headers:
            head_headers = exc.headers
            final_url = exc.geturl()
            status_code = exc.code
    except (error.URLError, TimeoutError, ValueError, OSError) as exc:
        head_error = f"HEAD failed: {exc}"

    if (
        head_headers
        and status_supports_record(status_code)
        and is_probably_download(final_url, head_headers, link.reason, link.anchor_text)
    ):
        size = parse_content_length(head_headers)
        return DownloadRecord(
            url=link.url,
            final_url=final_url,
            source_page=link.source_page,
            reason=link.reason,
            anchor_text=link.anchor_text,
            method=link.method,
            request_data=link.request_data,
            status_code=status_code,
            content_type=head_headers.get_content_type().lower(),
            size_bytes=size,
            filename=extract_filename(final_url, head_headers),
            error_message=head_error,
        )

    try:
        with make_request(
            link.url,
            settings=settings,
            method="GET",
            extra_headers={"Range": "bytes=0-0"},
        ) as response:
            headers = response.headers
            final_url = response.geturl()
            status_code = getattr(response, "status", None)
            if not status_supports_record(status_code):
                return None
            if not is_probably_download(final_url, headers, link.reason, link.anchor_text):
                return None
            size = parse_content_range_total(headers) or parse_content_length(headers)
            return DownloadRecord(
                url=link.url,
                final_url=final_url,
                source_page=link.source_page,
                reason=link.reason,
                anchor_text=link.anchor_text,
                method=link.method,
                request_data=link.request_data,
                status_code=status_code,
                content_type=headers.get_content_type().lower(),
                size_bytes=size,
                filename=extract_filename(final_url, headers),
                error_message=head_error,
            )
    except error.HTTPError as exc:
        headers = exc.headers
        if (
            headers
            and status_supports_record(exc.code)
            and is_probably_download(exc.geturl(), headers, link.reason, link.anchor_text)
        ):
            size = parse_content_range_total(headers) or parse_content_length(headers)
            return DownloadRecord(
                url=link.url,
                final_url=exc.geturl(),
                source_page=link.source_page,
                reason=link.reason,
                anchor_text=link.anchor_text,
                method=link.method,
                request_data=link.request_data,
                status_code=exc.code,
                content_type=headers.get_content_type().lower(),
                size_bytes=size,
                filename=extract_filename(exc.geturl(), headers),
                error_message=head_error or f"GET failed with HTTP {exc.code}",
            )
        return None
    except (error.URLError, TimeoutError, ValueError, OSError) as exc:
        if head_error is None:
            head_error = f"GET failed: {exc}"
        return DownloadRecord(
            url=link.url,
            final_url=final_url,
            source_page=link.source_page,
            reason=link.reason,
            anchor_text=link.anchor_text,
            method=link.method,
            request_data=link.request_data,
            status_code=status_code,
            content_type=None,
            size_bytes=None,
            filename=posixpath.basename(parse.urlparse(final_url).path) or None,
            error_message=head_error,
        )


def dedupe_records(records: Iterable[DownloadRecord]) -> list[DownloadRecord]:
    best_by_final_url: dict[tuple[str, str, tuple[tuple[str, str], ...]], DownloadRecord] = {}
    for record in records:
        key = (record.final_url, record.method, record.request_data)
        existing = best_by_final_url.get(key)
        if existing is None:
            best_by_final_url[key] = record
            continue
        existing_size_known = existing.size_bytes is not None
        current_size_known = record.size_bytes is not None
        if current_size_known and not existing_size_known:
            best_by_final_url[key] = record
    return list(best_by_final_url.values())


def human_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size_bytes} B"


def summarize_extensions(records: Iterable[DownloadRecord]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for record in records:
        ext = file_extension(record.filename or record.final_url) or "[no extension]"
        counts[ext] += 1
    return counts


def build_summary(start_url: str, stats: CrawlStats, records: list[DownloadRecord]) -> dict[str, object]:
    known_sizes = [record.size_bytes for record in records if record.size_bytes is not None]
    unknown_count = sum(1 for record in records if record.size_bytes is None)
    total_bytes = sum(known_sizes)
    extension_counts = summarize_extensions(records)
    largest = sorted(
        [record for record in records if record.size_bytes is not None],
        key=lambda item: item.size_bytes or 0,
        reverse=True,
    )[:10]
    return {
        "start_url": start_url,
        "pages_scanned": stats.pages_scanned,
        "pages_blocked_by_robots": stats.pages_blocked_by_robots,
        "robots_enabled": stats.robots_enabled,
        "robots_url": stats.robots_url,
        "crawl_delay_seconds": stats.crawl_delay_seconds,
        "download_links_found": len(records),
        "known_sizes": len(known_sizes),
        "unknown_sizes": unknown_count,
        "total_known_bytes": total_bytes,
        "total_known_human": human_size(total_bytes),
        "is_lower_bound": unknown_count > 0,
        "extensions": dict(extension_counts.most_common()),
        "largest_files": [
            {
                "url": record.final_url,
                "method": record.method,
                "filename": record.filename,
                "size_bytes": record.size_bytes,
                "size_human": human_size(record.size_bytes or 0),
            }
            for record in largest
        ],
    }


def print_summary(summary: dict[str, object]) -> None:
    print(f"Start URL: {summary['start_url']}")
    print(f"HTML pages scanned: {summary['pages_scanned']}")
    if summary["robots_enabled"]:
        print(f"robots.txt: enabled ({summary['robots_url']})")
        if summary["crawl_delay_seconds"]:
            print(f"Crawl delay used: {summary['crawl_delay_seconds']} seconds")
        if summary["pages_blocked_by_robots"]:
            print(f"Pages blocked by robots.txt: {summary['pages_blocked_by_robots']}")
    else:
        print("robots.txt: not applied")
    print(f"Download links found: {summary['download_links_found']}")
    print(f"Known sizes: {summary['known_sizes']}")
    print(f"Unknown sizes: {summary['unknown_sizes']}")
    print(f"Total known download size: {summary['total_known_human']}")
    if summary["is_lower_bound"]:
        print("Note: total is a lower bound because some links did not expose file size.")

    extensions = summary["extensions"]
    if extensions:
        print("\nFiles by extension:")
        for ext, count in list(extensions.items())[:10]:
            print(f"  {ext}: {count}")

    largest = summary["largest_files"]
    if largest:
        print("\nLargest files:")
        for item in largest:
            name = item["filename"] or item["url"]
            print(f"  {item['size_human']:>10}  {name}")


def print_links(records: list[DownloadRecord]) -> None:
    print("\nDiscovered download links:")
    for record in sorted(records, key=lambda item: item.final_url):
        size = human_size(record.size_bytes) if record.size_bytes is not None else "unknown"
        content_type = record.content_type or "unknown type"
        payload = (
            " | "
            + parse.urlencode(list(record.request_data))
            if record.request_data
            else ""
        )
        print(f"- {size} | {record.method} | {content_type} | {record.final_url}{payload}")


def write_json_output(path_text: str, summary: dict[str, object], records: list[DownloadRecord]) -> Path:
    path = Path(path_text).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": summary,
        "records": [
            {
                "url": record.url,
                "final_url": record.final_url,
                "source_page": record.source_page,
                "reason": record.reason,
                "anchor_text": record.anchor_text,
                "method": record.method,
                "request_data": list(record.request_data),
                "status_code": record.status_code,
                "content_type": record.content_type,
                "size_bytes": record.size_bytes,
                "size_human": human_size(record.size_bytes) if record.size_bytes is not None else None,
                "filename": record.filename,
                "error_message": record.error_message,
            }
            for record in records
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def write_csv_output(path_text: str, records: list[DownloadRecord]) -> Path:
    path = Path(path_text).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "url",
                "final_url",
                "source_page",
                "reason",
                "anchor_text",
                "method",
                "request_data",
                "status_code",
                "content_type",
                "size_bytes",
                "size_human",
                "filename",
                "error_message",
            ],
        )
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "url": record.url,
                    "final_url": record.final_url,
                    "source_page": record.source_page,
                "reason": record.reason,
                "anchor_text": record.anchor_text,
                "method": record.method,
                "request_data": parse.urlencode(list(record.request_data)),
                "status_code": record.status_code,
                "content_type": record.content_type,
                    "size_bytes": record.size_bytes,
                    "size_human": human_size(record.size_bytes) if record.size_bytes is not None else "",
                    "filename": record.filename,
                    "error_message": record.error_message,
                }
            )
    return path


def main() -> int:
    args = parse_args()
    settings = RequestSettings(
        timeout=args.timeout,
        retries=max(args.retries, 0),
        backoff=max(args.backoff, 0.0),
        ssl_context=ssl._create_unverified_context() if args.insecure else None,
        crawl_workers=max(args.workers, 1),
    )

    try:
        summary, records, _stats = run_discovery(
            start_url=args.url,
            max_pages=max(args.max_pages, 1),
            allow_subdomains=args.allow_subdomains,
            settings=settings,
            ignore_robots=args.ignore_robots,
            inspect_workers=max(args.workers, 1),
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if not records:
        print("No likely download links were found.")
        return 0

    print_summary(summary)

    if args.show_links:
        print_links(records)

    if args.json_out:
        json_path = write_json_output(args.json_out, summary, records)
        print(f"\nWrote JSON output: {json_path}")

    if args.csv_out:
        csv_path = write_csv_output(args.csv_out, records)
        print(f"Wrote CSV output: {csv_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
