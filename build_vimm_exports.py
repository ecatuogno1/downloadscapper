#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
from pathlib import Path

from project_paths import default_workspace_dir

REPORT_PREFIX_BY_SYSTEM = {
    "CD-i": "cdi",
    "Dreamcast": "dreamcast",
    "GameCube": "gamecube",
    "Jaguar CD": "jaguarcd",
    "PS Portable": "psp",
    "PlayStation": "ps1",
    "PlayStation 2": "ps2",
    "PlayStation 3": "ps3",
    "Saturn": "saturn",
    "Sega CD": "segacd",
    "TurboGrafx-CD": "tgcd",
    "Wii": "wii",
    "WiiWare": "wiiware",
    "Xbox": "xbox",
    "Xbox 360": "xbox360",
    "Xbox 360 (Digital)": "x360-d",
}


def parse_args() -> argparse.Namespace:
    base_dir = default_workspace_dir()
    parser = argparse.ArgumentParser(
        description="Build derived Vimm exports from the master deduped CSV."
    )
    parser.add_argument(
        "--base-dir",
        default=str(base_dir),
        help=f"Workspace containing the Vimm files. Default: {base_dir}",
    )
    parser.add_argument(
        "--master-csv",
        default=None,
        help="Master deduped CSV path. Default: <base-dir>/vimm-master-deduped.csv",
    )
    return parser.parse_args()


def human_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size_bytes} B"


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "system"


def load_rows(master_csv: Path) -> list[dict[str, str]]:
    with master_csv.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def relative_to_base(path: Path, base_dir: Path) -> str:
    return path.relative_to(base_dir).as_posix()


def build_top_100(rows: list[dict[str, str]], top100_csv: Path, top100_md: Path) -> None:
    ranked = sorted(rows, key=lambda row: int(row["size_bytes"] or "0"), reverse=True)[:100]
    top_rows: list[dict[str, str]] = []
    lines = [
        "# Vimm Top 100 Largest Downloads",
        "",
        f"- Items listed: {len(ranked)}",
        "",
    ]

    for index, row in enumerate(ranked, start=1):
        top_rows.append(
            {
                "rank": str(index),
                "system_name": row["system_name"],
                "filename": row["filename"],
                "size_bytes": row["size_bytes"],
                "size_human": row["size_human"] or human_size(int(row["size_bytes"] or "0")),
                "source_page": row["source_page"],
                "final_url": row["final_url"],
                "request_data": row["request_data"],
            }
        )
        lines.append(
            f"- #{index}: {row['filename']} [{row['system_name']}] {row['size_human'] or human_size(int(row['size_bytes'] or '0'))}"
        )

    write_csv(
        top100_csv,
        top_rows,
        ["rank", "system_name", "filename", "size_bytes", "size_human", "source_page", "final_url", "request_data"],
    )
    top100_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_console_totals(rows: list[dict[str, str]], totals_csv: Path, totals_md: Path) -> None:
    totals: dict[str, dict[str, int | str]] = {}
    for row in rows:
        system_name = row["system_name"]
        size_bytes = int(row["size_bytes"] or "0")
        entry = totals.setdefault(
            system_name,
            {"system_name": system_name, "base_type": row["base_type"], "items": 0, "total_bytes": 0},
        )
        entry["items"] = int(entry["items"]) + 1
        entry["total_bytes"] = int(entry["total_bytes"]) + size_bytes

    total_rows = [
        {
            "system_name": info["system_name"],
            "base_type": info["base_type"],
            "items": str(info["items"]),
            "total_bytes": str(info["total_bytes"]),
            "total_human": human_size(int(info["total_bytes"])),
        }
        for info in sorted(totals.values(), key=lambda item: int(item["total_bytes"]), reverse=True)
    ]

    lines = [
        "# Vimm Console Totals",
        "",
        f"- Systems: {len(total_rows)}",
        f"- Total items: {sum(int(row['items']) for row in total_rows)}",
        f"- Total size: {human_size(sum(int(row['total_bytes']) for row in total_rows))}",
        "",
        "## Systems",
        "",
    ]
    for row in total_rows:
        lines.append(
            f"- {row['system_name']}: {row['items']} items, {row['total_human']} [{row['base_type']}]"
        )

    write_csv(totals_csv, total_rows, ["system_name", "base_type", "items", "total_bytes", "total_human"])
    totals_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_system_csvs(rows: list[dict[str, str]], system_dir: Path) -> None:
    system_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["system_name"], []).append(row)

    for system_name, system_rows in grouped.items():
        system_rows.sort(key=lambda row: int(row["size_bytes"] or "0"), reverse=True)
        output_path = system_dir / f"{slugify(system_name)}.csv"
        write_csv(
            output_path,
            system_rows,
            ["system_name", "base_type", "filename", "size_bytes", "size_human", "method", "final_url", "source_page", "request_data"],
        )


def copy_if_present(source: Path, destination: Path) -> bool:
    if not source.exists():
        return False
    shutil.copy2(source, destination)
    return True


def build_system_folders(rows: list[dict[str, str]], system_folder_dir: Path, base_dir: Path) -> None:
    system_folder_dir.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["system_name"], []).append(row)

    index_rows: list[dict[str, str]] = []
    for system_name, system_rows in sorted(grouped.items()):
        system_rows.sort(key=lambda row: int(row["size_bytes"] or "0"), reverse=True)
        folder_slug = slugify(system_name)
        folder = system_folder_dir / folder_slug
        folder.mkdir(parents=True, exist_ok=True)

        downloads_csv = folder / "downloads.csv"
        write_csv(
            downloads_csv,
            system_rows,
            ["system_name", "base_type", "filename", "size_bytes", "size_human", "method", "final_url", "source_page", "request_data"],
        )

        total_bytes = sum(int(row["size_bytes"] or "0") for row in system_rows)
        prefix = REPORT_PREFIX_BY_SYSTEM.get(system_name)
        has_report_csv = False
        has_report_json = False
        has_report_summary = False
        if prefix:
            has_report_csv = copy_if_present(
                base_dir / f"vimm-{prefix}-report.csv",
                folder / "crawl-report.csv",
            )
            has_report_json = copy_if_present(
                base_dir / f"vimm-{prefix}-report.json",
                folder / "crawl-report.json",
            )
            has_report_summary = copy_if_present(
                base_dir / f"vimm-{prefix}-summary.txt",
                folder / "crawl-summary.txt",
            )

        metadata = {
            "system_name": system_name,
            "slug": folder_slug,
            "directory": relative_to_base(folder, base_dir),
            "item_count": len(system_rows),
            "total_bytes": total_bytes,
            "total_human": human_size(total_bytes),
            "base_type": system_rows[0]["base_type"] if system_rows else "",
            "downloads_csv": relative_to_base(downloads_csv, base_dir),
            "source_report_prefix": prefix,
            "has_crawl_report_csv": has_report_csv,
            "has_crawl_report_json": has_report_json,
            "has_crawl_summary_txt": has_report_summary,
        }
        (folder / "metadata.json").write_text(
            json.dumps(metadata, indent=2) + "\n",
            encoding="utf-8",
        )
        (folder / "README.md").write_text(
            "\n".join(
                [
                    f"# {system_name}",
                    "",
                    f"- Items: {len(system_rows)}",
                    f"- Total size: {human_size(total_bytes)}",
                    f"- Canonical download list: `downloads.csv`",
                    f"- Crawl report CSV: {'present' if has_report_csv else 'not available'}",
                    f"- Crawl report JSON: {'present' if has_report_json else 'not available'}",
                    f"- Crawl summary TXT: {'present' if has_report_summary else 'not available'}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        index_rows.append(
            {
                "system_name": system_name,
                "slug": folder_slug,
                "items": str(len(system_rows)),
                "total_bytes": str(total_bytes),
                "total_human": human_size(total_bytes),
                "directory": relative_to_base(folder, base_dir),
                "has_crawl_report_csv": "yes" if has_report_csv else "no",
                "has_crawl_report_json": "yes" if has_report_json else "no",
                "has_crawl_summary_txt": "yes" if has_report_summary else "no",
            }
        )

    write_csv(
        system_folder_dir / "index.csv",
        index_rows,
        [
            "system_name",
            "slug",
            "items",
            "total_bytes",
            "total_human",
            "directory",
            "has_crawl_report_csv",
            "has_crawl_report_json",
            "has_crawl_summary_txt",
        ],
    )
    (system_folder_dir / "README.md").write_text(
        "\n".join(
            [
                "# Vimm Systems",
                "",
                f"- Systems: {len(index_rows)}",
                "- One folder per system.",
                "- Each folder includes `downloads.csv`, `metadata.json`, and `README.md`.",
                "- Dedicated crawl artifacts are copied in when they exist.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def build_download_links(rows: list[dict[str, str]], links_csv: Path) -> None:
    link_rows = [
        {
            "system_name": row["system_name"],
            "filename": row["filename"],
            "size_human": row["size_human"],
            "size_bytes": row["size_bytes"],
            "source_page": row["source_page"],
            "final_url": row["final_url"],
            "method": row["method"],
            "request_data": row["request_data"],
        }
        for row in rows
    ]
    link_rows.sort(
        key=lambda row: (row["system_name"], -int(row["size_bytes"] or "0"), row["filename"])
    )
    write_csv(
        links_csv,
        link_rows,
        [
            "system_name",
            "filename",
            "size_human",
            "size_bytes",
            "source_page",
            "final_url",
            "method",
            "request_data",
        ],
    )


def main() -> int:
    args = parse_args()
    base_dir = Path(args.base_dir).expanduser().resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    master_csv = Path(args.master_csv).expanduser().resolve() if args.master_csv else base_dir / "vimm-master-deduped.csv"
    top100_csv = base_dir / "vimm-top-100-largest.csv"
    top100_md = base_dir / "vimm-top-100-largest.md"
    totals_csv = base_dir / "vimm-console-totals.csv"
    totals_md = base_dir / "vimm-console-totals.md"
    links_csv = base_dir / "vimm-download-links.csv"
    system_dir = base_dir / "vimm-system-csvs"
    system_folder_dir = base_dir / "vimm-systems"

    rows = load_rows(master_csv)
    build_top_100(rows, top100_csv, top100_md)
    build_console_totals(rows, totals_csv, totals_md)
    build_system_csvs(rows, system_dir)
    build_system_folders(rows, system_folder_dir, base_dir)
    build_download_links(rows, links_csv)
    print(top100_csv)
    print(top100_md)
    print(totals_csv)
    print(totals_md)
    print(links_csv)
    print(system_dir)
    print(system_folder_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
