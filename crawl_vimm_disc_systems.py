#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

from project_paths import default_project_dir, default_workspace_dir

SYSTEM_NAMES = {
    "TGCD": "TurboGrafx-CD",
    "CDi": "CD-i",
    "SegaCD": "Sega CD",
    "JaguarCD": "Jaguar CD",
    "Saturn": "Saturn",
    "PS1": "PlayStation",
    "Dreamcast": "Dreamcast",
    "PS2": "PlayStation 2",
    "GameCube": "GameCube",
    "Xbox": "Xbox",
    "Xbox360": "Xbox 360",
    "X360-D": "Xbox 360 (Digital)",
    "PS3": "PlayStation 3",
    "Wii": "Wii",
    "WiiWare": "WiiWare",
    "PSP": "PS Portable",
}

DEFAULT_SYSTEMS = [
    "TGCD",
    "CDi",
    "SegaCD",
    "JaguarCD",
    "Saturn",
    "PS1",
    "Dreamcast",
    "PS2",
    "GameCube",
    "Xbox",
    "Xbox360",
    "X360-D",
    "PS3",
    "Wii",
    "WiiWare",
    "PSP",
]


def parse_args() -> argparse.Namespace:
    base_dir = default_workspace_dir()
    parser = argparse.ArgumentParser(
        description="Batch crawl disc-based Vimm systems and summarize results."
    )
    parser.add_argument(
        "--base-dir",
        default=str(base_dir),
        help=f"Workspace to read/write reports from. Default: {base_dir}",
    )
    parser.add_argument(
        "--systems",
        nargs="+",
        default=DEFAULT_SYSTEMS,
        help="System codes to crawl. Default: known disc-based systems",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=5000,
        help="Max pages per system crawl. Default: 5000",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=16,
        help="Workers per crawl. Default: 16",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Reuse existing per-system JSON reports when present.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification in the underlying crawler.",
    )
    return parser.parse_args()


def safe_system_slug(system_code: str) -> str:
    return system_code.lower().replace("/", "-")


def human_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size_bytes} B"


def relative_to_base(path: Path, base_dir: Path) -> str:
    return path.relative_to(base_dir).as_posix()


def run_crawl(system_code: str, args: argparse.Namespace, base_dir: Path, crawler: Path) -> Path:
    slug = safe_system_slug(system_code)
    summary_path = base_dir / f"vimm-{slug}-summary.txt"
    json_path = base_dir / f"vimm-{slug}-report.json"
    csv_path = base_dir / f"vimm-{slug}-report.csv"

    if args.skip_existing and json_path.exists():
        return json_path

    cmd = [
        sys.executable,
        str(crawler),
        f"https://vimm.net/vault/{system_code}",
        "--max-pages",
        str(args.max_pages),
        "--workers",
        str(args.workers),
        "--json-out",
        str(json_path),
        "--csv-out",
        str(csv_path),
    ]
    if args.insecure:
        cmd.append("--insecure")

    with summary_path.open("w", encoding="utf-8") as summary_file:
        subprocess.run(cmd, check=True, stdout=summary_file)
    return json_path


def load_summary(json_path: Path) -> dict[str, object]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    return payload["summary"]


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    crawler = default_project_dir() / "website_download_summary.py"
    if not crawler.exists():
        raise FileNotFoundError(f"Missing crawler script: {crawler}")

    rows: list[dict[str, object]] = []

    for system_code in args.systems:
        json_path = run_crawl(system_code, args, base_dir, crawler)
        summary = load_summary(json_path)
        rows.append(
            {
                "system_code": system_code,
                "system_name": SYSTEM_NAMES.get(system_code, system_code),
                "downloads_found": summary["download_links_found"],
                "known_sizes": summary["known_sizes"],
                "unknown_sizes": summary["unknown_sizes"],
                "total_known_bytes": summary["total_known_bytes"],
                "total_known_human": summary["total_known_human"],
                "pages_scanned": summary["pages_scanned"],
                "json_report": relative_to_base(json_path, base_dir),
                "summary_file": relative_to_base(
                    base_dir / f"vimm-{safe_system_slug(system_code)}-summary.txt",
                    base_dir,
                ),
            }
        )

    rows.sort(key=lambda row: int(row["total_known_bytes"]), reverse=True)

    csv_path = base_dir / "vimm-disc-systems-summary.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "system_code",
                "system_name",
                "downloads_found",
                "known_sizes",
                "unknown_sizes",
                "total_known_bytes",
                "total_known_human",
                "pages_scanned",
                "json_report",
                "summary_file",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    total_bytes = sum(int(row["total_known_bytes"]) for row in rows)
    total_downloads = sum(int(row["downloads_found"]) for row in rows)
    md_path = base_dir / "vimm-disc-systems-summary.md"
    lines = [
        "# Vimm Disc Systems Summary",
        "",
        f"- Systems crawled: {len(rows)}",
        f"- Downloads found: {total_downloads}",
        f"- Total known size: {human_size(total_bytes)}",
        "",
        "## Systems",
        "",
    ]
    for row in rows:
        lines.append(f"### {row['system_name']}")
        lines.append(f"- System code: {row['system_code']}")
        lines.append(f"- Downloads found: {row['downloads_found']}")
        lines.append(f"- Total known size: {row['total_known_human']}")
        lines.append(f"- HTML pages scanned: {row['pages_scanned']}")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")

    print(csv_path)
    print(md_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
