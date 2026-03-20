#!/usr/bin/env python3

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import re
import ssl
from collections import defaultdict
from pathlib import Path
from urllib import error, request

from project_paths import default_workspace_dir

DEFAULT_USER_AGENT = "Mozilla/5.0 (compatible; VimmPlatformEnricher/1.0; +https://localhost)"


KNOWN_TYPE_MAP = {
    ".md": "Genesis / Mega Drive ROM",
    ".sms": "Sega Master System ROM",
    ".a52": "Atari 5200 ROM",
    ".bin": "Binary cartridge ROM",
    ".j64": "Atari Jaguar ROM",
    ".a78": "Atari 7800 ROM",
}

SYSTEM_CODE_NAMES = {
    "3DS": "Nintendo 3DS",
    "32X": "Sega 32X",
    "Atari2600": "Atari 2600",
    "Atari5200": "Atari 5200",
    "Atari7800": "Atari 7800",
    "CDi": "CD-i",
    "DS": "Nintendo DS",
    "Dreamcast": "Dreamcast",
    "GBA": "Game Boy Advance",
    "GBC": "Game Boy Color",
    "GB": "Game Boy",
    "GG": "Game Gear",
    "GameCube": "GameCube",
    "Genesis": "Genesis",
    "Jaguar": "Jaguar",
    "JaguarCD": "Jaguar CD",
    "Lynx": "Lynx",
    "N64": "Nintendo 64",
    "NES": "Nintendo",
    "PS1": "PlayStation",
    "PS2": "PlayStation 2",
    "PS3": "PlayStation 3",
    "PSP": "PS Portable",
    "SMS": "Master System",
    "SNES": "Super Nintendo",
    "Saturn": "Saturn",
    "SegaCD": "Sega CD",
    "TG16": "TurboGrafx-16",
    "TGCD": "TurboGrafx-CD",
    "VB": "Virtual Boy",
    "Wii": "Wii",
    "WiiWare": "WiiWare",
    "Xbox": "Xbox",
    "Xbox360": "Xbox 360",
    "X360-D": "Xbox 360 (Digital)",
}


def parse_args() -> argparse.Namespace:
    base_dir = default_workspace_dir()
    archive_dir = base_dir / "archive"
    parser = argparse.ArgumentParser(
        description="Enrich the Vimm crawl report with platform/system labels."
    )
    parser.add_argument(
        "--json-in",
        default=str(archive_dir / "vimm-vault-report.json"),
        help="Input JSON report from website_download_summary.py",
    )
    parser.add_argument(
        "--csv-out",
        default=str(archive_dir / "vimm-vault-report-platforms.csv"),
        help="Output CSV with platform labels",
    )
    parser.add_argument(
        "--md-out",
        default=str(archive_dir / "vimm-vault-platform-summary.md"),
        help="Output markdown summary",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Parallel workers for source-page resolution. Default: 16",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Request timeout in seconds. Default: 20",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification.",
    )
    return parser.parse_args()


def human_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size_bytes} B"


def detect_ext(filename: str) -> str:
    if not filename or "." not in filename:
        return "[no extension]"
    return "." + filename.rsplit(".", 1)[1].lower()


def base_type_from_filename(filename: str) -> str:
    ext = detect_ext(filename)
    if ext in KNOWN_TYPE_MAP:
        return KNOWN_TYPE_MAP[ext]
    if ext == "[no extension]":
        return "Disc-based / extensionless game download"
    if " " in ext or "(" in ext or ")" in ext:
        return "Disc-based / extensionless game download"
    return f"Other / unclassified ({ext})"


def fetch_html(url: str, timeout: float, context: ssl.SSLContext | None) -> str | None:
    req = request.Request(url, headers={"User-Agent": DEFAULT_USER_AGENT})
    try:
        with request.urlopen(req, timeout=timeout, context=context) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except (error.HTTPError, error.URLError, TimeoutError, ValueError, OSError):
        return None


def parse_platform_from_html(html: str) -> tuple[str | None, str | None]:
    system_match = re.search(
        r'name="system"\s+value="([^"]+)"',
        html,
        re.IGNORECASE,
    )
    title_match = re.search(
        r'<div class="sectionTitle">([^<]+)</div>',
        html,
        re.IGNORECASE,
    )
    code = system_match.group(1).strip() if system_match else None
    title = title_match.group(1).strip() if title_match else None
    if code and not title:
        title = SYSTEM_CODE_NAMES.get(code, code)
    return code, title


def resolve_platform(
    url: str,
    timeout: float,
    context: ssl.SSLContext | None,
) -> tuple[str | None, str | None]:
    html = fetch_html(url, timeout=timeout, context=context)
    if not html:
        return None, None
    return parse_platform_from_html(html)


def main() -> int:
    args = parse_args()
    json_in = Path(args.json_in).expanduser()
    csv_out = Path(args.csv_out).expanduser()
    md_out = Path(args.md_out).expanduser()
    payload = json.loads(json_in.read_text(encoding="utf-8"))
    records = payload["records"]
    summary = payload["summary"]
    context = ssl._create_unverified_context() if args.insecure else None

    disc_source_pages = {
        record.get("source_page")
        for record in records
        if base_type_from_filename((record.get("filename") or "").strip())
        == "Disc-based / extensionless game download"
        and isinstance(record.get("source_page"), str)
        and "/vault/" in record["source_page"]
        and record["source_page"].rstrip("/").split("/")[-1].isdigit()
    }
    urls_to_resolve = sorted(disc_source_pages)

    resolved: dict[str, tuple[str | None, str | None]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(args.workers, 1)) as executor:
        future_map = {
            executor.submit(resolve_platform, url, args.timeout, context): url
            for url in urls_to_resolve
        }
        for future in concurrent.futures.as_completed(future_map):
            url = future_map[future]
            resolved[url] = future.result()

    rows: list[dict[str, object]] = []
    by_platform = defaultdict(lambda: {"count": 0, "size": 0, "examples": []})
    by_disc_platform = defaultdict(lambda: {"count": 0, "size": 0, "examples": []})

    for record in records:
        filename = (record.get("filename") or "").strip()
        base_type = base_type_from_filename(filename)
        source_page = record.get("source_page") or ""
        platform_code, platform_name = resolved.get(source_page, (None, None))
        if base_type != "Disc-based / extensionless game download":
            if not platform_name:
                platform_name = base_type
            if not platform_code:
                platform_code = "LOCAL"
        else:
            if not platform_name:
                platform_name = "Unknown platform"
            if not platform_code:
                platform_code = "UNKNOWN"
        size_bytes = int(record.get("size_bytes") or 0)
        request_data = record.get("request_data") or []
        request_text = "&".join(
            f"{item[0]}={item[1]}"
            for item in request_data
            if isinstance(item, list) and len(item) == 2
        )

        rows.append(
            {
                "platform_code": platform_code,
                "platform_name": platform_name,
                "base_type": base_type,
                "filename": filename,
                "size_bytes": size_bytes,
                "size_human": record.get("size_human") or human_size(size_bytes),
                "method": record.get("method") or "",
                "final_url": record.get("final_url") or "",
                "source_page": source_page,
                "request_data": request_text,
            }
        )

        bucket = by_platform[platform_name]
        bucket["count"] += 1
        bucket["size"] += size_bytes
        if filename and len(bucket["examples"]) < 5 and filename not in bucket["examples"]:
            bucket["examples"].append(filename)

        if base_type == "Disc-based / extensionless game download":
            disc_bucket = by_disc_platform[platform_name]
            disc_bucket["count"] += 1
            disc_bucket["size"] += size_bytes
            if (
                filename
                and len(disc_bucket["examples"]) < 5
                and filename not in disc_bucket["examples"]
            ):
                disc_bucket["examples"].append(filename)

    rows.sort(
        key=lambda row: (
            row["platform_name"],
            -int(row["size_bytes"]),
            str(row["filename"]),
        )
    )

    csv_out.parent.mkdir(parents=True, exist_ok=True)
    with csv_out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "platform_code",
                "platform_name",
                "base_type",
                "filename",
                "size_bytes",
                "size_human",
                "method",
                "final_url",
                "source_page",
                "request_data",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    lines: list[str] = []
    lines.append("# Vimm Vault Platform Summary")
    lines.append("")
    lines.append(f"- Total downloads found: {summary['download_links_found']}")
    lines.append(f"- Total known size: {summary['total_known_human']}")
    lines.append(f"- HTML pages scanned: {summary['pages_scanned']}")
    lines.append("")
    lines.append("## Disc-Based Downloads By Platform")
    lines.append("")
    for platform_name, data in sorted(
        by_disc_platform.items(),
        key=lambda item: (-item[1]["size"], -item[1]["count"], item[0]),
    ):
        lines.append(f"### {platform_name}")
        lines.append(f"- Count: {data['count']}")
        lines.append(f"- Total size: {human_size(data['size'])}")
        if data["examples"]:
            lines.append("- Examples: " + " | ".join(data["examples"]))
        lines.append("")

    lines.append("## All Downloads By Platform")
    lines.append("")
    for platform_name, data in sorted(
        by_platform.items(),
        key=lambda item: (-item[1]["size"], -item[1]["count"], item[0]),
    ):
        lines.append(f"### {platform_name}")
        lines.append(f"- Count: {data['count']}")
        lines.append(f"- Total size: {human_size(data['size'])}")
        if data["examples"]:
            lines.append("- Examples: " + " | ".join(data["examples"]))
        lines.append("")

    md_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.write_text("\n".join(lines), encoding="utf-8")
    print(csv_out)
    print(md_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
