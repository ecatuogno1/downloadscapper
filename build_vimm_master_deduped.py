#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from project_paths import default_workspace_dir

ROOT_NON_DISC_SYSTEMS = {
    "Genesis",
    "Jaguar",
    "Master System",
    "Atari 7800",
    "Atari 5200",
}

ROOT_OPTIONAL_DISC_SYSTEMS = {
    "PlayStation",
    "Saturn",
}

OUTPUT_FIELDS = [
    "system_name",
    "base_type",
    "filename",
    "size_bytes",
    "size_human",
    "method",
    "final_url",
    "source_page",
    "request_data",
]


def parse_args() -> argparse.Namespace:
    base_dir = default_workspace_dir()
    parser = argparse.ArgumentParser(
        description="Build the master deduped Vimm CSV and markdown summary."
    )
    parser.add_argument(
        "--base-dir",
        default=str(base_dir),
        help=f"Workspace containing the Vimm report files. Default: {base_dir}",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Output CSV path. Default: <base-dir>/vimm-master-deduped.csv",
    )
    parser.add_argument(
        "--output-md",
        default=None,
        help="Output markdown path. Default: <base-dir>/vimm-master-deduped-summary.md",
    )
    parser.add_argument(
        "--root-platform-csv",
        default=None,
        help="Root-vault platform CSV path. Default: <base-dir>/archive/vimm-vault-report-platforms.csv",
    )
    return parser.parse_args()


def build_targeted_reports(base_dir: Path) -> dict[str, Path]:
    return {
        "TurboGrafx-CD": base_dir / "vimm-tgcd-report.csv",
        "CD-i": base_dir / "vimm-cdi-report.csv",
        "Sega CD": base_dir / "vimm-segacd-report.csv",
        "Jaguar CD": base_dir / "vimm-jaguarcd-report.csv",
        "Saturn": base_dir / "vimm-saturn-report.csv",
        "PlayStation": base_dir / "vimm-ps1-report.csv",
        "Dreamcast": base_dir / "vimm-dreamcast-report.csv",
        "PlayStation 2": base_dir / "vimm-ps2-report.csv",
        "GameCube": base_dir / "vimm-gamecube-report.csv",
        "Xbox": base_dir / "vimm-xbox-report.csv",
        "Xbox 360": base_dir / "vimm-xbox360-report.csv",
        "Xbox 360 (Digital)": base_dir / "vimm-x360-d-report.csv",
        "PlayStation 3": base_dir / "vimm-ps3-report.csv",
        "Wii": base_dir / "vimm-wii-report.csv",
        "WiiWare": base_dir / "vimm-wiiware-report.csv",
        "PS Portable": base_dir / "vimm-psp-report.csv",
    }


def human_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size_bytes} B"


def normalize_row(row: dict[str, str], system_name: str, base_type: str) -> dict[str, str]:
    size_bytes = row.get("size_bytes") or "0"
    try:
        size_int = int(size_bytes)
    except ValueError:
        size_int = 0
    return {
        "system_name": system_name,
        "base_type": base_type,
        "filename": row.get("filename", ""),
        "size_bytes": str(size_int),
        "size_human": row.get("size_human") or human_size(size_int),
        "method": row.get("method", ""),
        "final_url": row.get("final_url", ""),
        "source_page": row.get("source_page", ""),
        "request_data": row.get("request_data", ""),
    }


def load_root_rows(
    root_platform_csv: Path,
    targeted_reports: dict[str, Path],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if not root_platform_csv.exists():
        return rows

    dedicated_present = {
        system_name for system_name, path in targeted_reports.items() if path.exists()
    }

    with root_platform_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            system_name = row["platform_name"]
            if system_name in ROOT_NON_DISC_SYSTEMS:
                rows.append(
                    {
                        "system_name": system_name,
                        "base_type": row.get("base_type", "non-disc"),
                        "filename": row.get("filename", ""),
                        "size_bytes": row.get("size_bytes", "0"),
                        "size_human": row.get("size_human", ""),
                        "method": row.get("method", ""),
                        "final_url": row.get("final_url", ""),
                        "source_page": row.get("source_page", ""),
                        "request_data": row.get("request_data", ""),
                    }
                )
                continue

            if system_name in ROOT_OPTIONAL_DISC_SYSTEMS and system_name not in dedicated_present:
                rows.append(
                    {
                        "system_name": system_name,
                        "base_type": row.get("base_type", "disc"),
                        "filename": row.get("filename", ""),
                        "size_bytes": row.get("size_bytes", "0"),
                        "size_human": row.get("size_human", ""),
                        "method": row.get("method", ""),
                        "final_url": row.get("final_url", ""),
                        "source_page": row.get("source_page", ""),
                        "request_data": row.get("request_data", ""),
                    }
                )
    return rows


def load_targeted_rows(targeted_reports: dict[str, Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for system_name, report_path in targeted_reports.items():
        if not report_path.exists():
            continue
        with report_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append(normalize_row(row, system_name, "disc"))
    return rows


def dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str, str]] = set()
    deduped: list[dict[str, str]] = []
    for row in rows:
        key = (
            row["system_name"],
            row["filename"],
            row["request_data"],
            row["final_url"],
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def summarize(rows: list[dict[str, str]]) -> str:
    total_bytes = 0
    disc_rows = 0
    disc_bytes = 0
    non_disc_rows = 0
    non_disc_bytes = 0
    systems: dict[str, dict[str, int | str]] = {}

    for row in rows:
        size_bytes = int(row["size_bytes"] or "0")
        total_bytes += size_bytes
        entry = systems.setdefault(
            row["system_name"],
            {"rows": 0, "bytes": 0, "base_type": row["base_type"]},
        )
        entry["rows"] = int(entry["rows"]) + 1
        entry["bytes"] = int(entry["bytes"]) + size_bytes

        if row["base_type"] == "disc":
            disc_rows += 1
            disc_bytes += size_bytes
        else:
            non_disc_rows += 1
            non_disc_bytes += size_bytes

    sorted_systems = sorted(
        systems.items(),
        key=lambda item: int(item[1]["bytes"]),
        reverse=True,
    )

    lines = [
        "# Vimm Master Deduped Summary",
        "",
        f"- Total deduped rows: {len(rows)}",
        f"- Total deduped size: {human_size(total_bytes)}",
        f"- Disc rows: {disc_rows} totaling {human_size(disc_bytes)}",
        f"- Non-disc rows: {non_disc_rows} totaling {human_size(non_disc_bytes)}",
        "",
        "## Systems",
        "",
    ]

    for system_name, info in sorted_systems:
        lines.append(
            f"- {system_name}: {info['rows']} rows, {human_size(int(info['bytes']))} [{info['base_type']}]"
        )

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This file excludes overlapping root-vault rows when a dedicated system report exists.",
            "- Root-vault rows are retained only for non-disc systems, plus PS1/Saturn as fallback if their dedicated reports are absent.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    output_csv = Path(args.output_csv).expanduser().resolve() if args.output_csv else base_dir / "vimm-master-deduped.csv"
    output_md = Path(args.output_md).expanduser().resolve() if args.output_md else base_dir / "vimm-master-deduped-summary.md"
    root_platform_csv = (
        Path(args.root_platform_csv).expanduser().resolve()
        if args.root_platform_csv
        else base_dir / "archive" / "vimm-vault-report-platforms.csv"
    )
    targeted_reports = build_targeted_reports(base_dir)

    rows = dedupe_rows(load_root_rows(root_platform_csv, targeted_reports) + load_targeted_rows(targeted_reports))

    rows.sort(
        key=lambda row: (row["system_name"], -int(row["size_bytes"] or "0"), row["filename"])
    )

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)

    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    output_md.write_text(summarize(rows), encoding="utf-8")

    print(output_csv)
    print(output_md)
    print(f"ROWS {len(rows)}")
    print(f"BYTES {sum(int(row['size_bytes'] or '0') for row in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
