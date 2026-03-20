#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from project_paths import default_workspace_dir


DEFAULT_DOWNLOADS_ROOT = Path.home() / "Downloads" / "downloadscapper-scrapes"
DEFAULT_DB_NAME = "download-index.sqlite3"
SCHEMA_VERSION = 1
REPORT_SYSTEM_BY_STEM = {
    "vimm-cdi-report": "CD-i",
    "vimm-dreamcast-report": "Dreamcast",
    "vimm-gamecube-report": "GameCube",
    "vimm-jaguarcd-report": "Jaguar CD",
    "vimm-ps1-report": "PlayStation",
    "vimm-ps2-report": "PlayStation 2",
    "vimm-ps3-report": "PlayStation 3",
    "vimm-psp-report": "PS Portable",
    "vimm-saturn-report": "Saturn",
    "vimm-segacd-report": "Sega CD",
    "vimm-tgcd-report": "TurboGrafx-CD",
    "vimm-wii-report": "Wii",
    "vimm-wiiware-report": "WiiWare",
    "vimm-x360-d-report": "Xbox 360 (Digital)",
    "vimm-xbox-report": "Xbox",
    "vimm-xbox360-report": "Xbox 360",
}


@dataclass(frozen=True)
class SourceContext:
    path: Path
    source_type: str
    label: str
    inferred_system_name: str
    inferred_base_type: str


def parse_args() -> argparse.Namespace:
    base_dir = default_workspace_dir()
    parser = argparse.ArgumentParser(
        description="Build and query an indexed SQLite database of discovered download links."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build_parser = subparsers.add_parser(
        "build",
        help="Rebuild the SQLite download index from repo CSVs and saved scrape CSVs.",
    )
    build_parser.add_argument(
        "--base-dir",
        default=str(base_dir),
        help=f"Workspace containing generated CSVs. Default: {base_dir}",
    )
    build_parser.add_argument(
        "--downloads-root",
        default=str(DEFAULT_DOWNLOADS_ROOT),
        help=(
            "Saved scrape archive root to ingest. "
            f"Default: {DEFAULT_DOWNLOADS_ROOT}"
        ),
    )
    build_parser.add_argument(
        "--db",
        default=None,
        help=f"Output SQLite database path. Default: <base-dir>/{DEFAULT_DB_NAME}",
    )
    build_parser.add_argument(
        "--skip-saved-scrapes",
        action="store_true",
        help="Only ingest CSVs found inside the local workspace.",
    )

    stats_parser = subparsers.add_parser(
        "stats",
        help="Show summary stats for an existing SQLite download index.",
    )
    stats_parser.add_argument(
        "--db",
        default=str(base_dir / DEFAULT_DB_NAME),
        help=f"SQLite database path. Default: {base_dir / DEFAULT_DB_NAME}",
    )

    search_parser = subparsers.add_parser(
        "search",
        help="Search the indexed downloads by filename, system, page, or URL.",
    )
    search_parser.add_argument("query", help="FTS query string.")
    search_parser.add_argument(
        "--db",
        default=str(base_dir / DEFAULT_DB_NAME),
        help=f"SQLite database path. Default: {base_dir / DEFAULT_DB_NAME}",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of matches to print. Default: 20",
    )
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def parse_int(value: object) -> int:
    text = normalize_text(value)
    if not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def human_size(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size_bytes} B"


def relative_label(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def extract_domain(*candidates: str) -> str:
    for candidate in candidates:
        text = normalize_text(candidate)
        if not text:
            continue
        parsed = urlparse(text)
        if parsed.netloc:
            return parsed.netloc.lower()
    return ""


def dedupe_key(payload: dict[str, str | int]) -> str:
    key_fields = (
        normalize_text(payload.get("final_url")),
        normalize_text(payload.get("request_data")),
        normalize_text(payload.get("source_page")),
        normalize_text(payload.get("filename")),
    )
    return hashlib.sha256("\x1f".join(key_fields).encode("utf-8")).hexdigest()


def csv_fieldnames(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or [])


def looks_like_download_csv(path: Path) -> bool:
    try:
        fieldnames = set(csv_fieldnames(path))
    except (OSError, UnicodeDecodeError, csv.Error):
        return False
    has_filename = "filename" in fieldnames
    has_link = "final_url" in fieldnames or "url" in fieldnames
    return has_filename and has_link


def load_vimm_metadata(base_dir: Path) -> dict[str, dict[str, str]]:
    metadata: dict[str, dict[str, str]] = {}
    systems_dir = base_dir / "vimm-systems"
    if not systems_dir.exists():
        return metadata
    for metadata_path in systems_dir.glob("*/metadata.json"):
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        slug = normalize_text(payload.get("slug"))
        if not slug:
            continue
        metadata[slug] = {
            "system_name": normalize_text(payload.get("system_name")),
            "base_type": normalize_text(payload.get("base_type")),
        }
    return metadata


def infer_context(
    path: Path,
    base_dir: Path,
    downloads_root: Path,
    vimm_metadata: dict[str, dict[str, str]],
) -> SourceContext:
    source_type = "repo-csv"
    label_root = base_dir
    if downloads_root.exists():
        try:
            path.relative_to(downloads_root)
        except ValueError:
            pass
        else:
            source_type = "saved-scrape-csv"
            label_root = downloads_root

    inferred_system_name = ""
    inferred_base_type = ""
    stem = path.stem
    if stem in REPORT_SYSTEM_BY_STEM:
        inferred_system_name = REPORT_SYSTEM_BY_STEM[stem]
        inferred_base_type = "disc"

    if "vimm-systems" in path.parts:
        try:
            slug = path.parts[path.parts.index("vimm-systems") + 1]
        except (ValueError, IndexError):
            slug = ""
        if slug and slug in vimm_metadata:
            inferred_system_name = vimm_metadata[slug]["system_name"] or inferred_system_name
            inferred_base_type = vimm_metadata[slug]["base_type"] or inferred_base_type

    return SourceContext(
        path=path,
        source_type=source_type,
        label=relative_label(path, label_root),
        inferred_system_name=inferred_system_name,
        inferred_base_type=inferred_base_type,
    )


def iter_candidate_csvs(base_dir: Path, downloads_root: Path, skip_saved_scrapes: bool) -> Iterable[SourceContext]:
    vimm_metadata = load_vimm_metadata(base_dir)
    seen: set[Path] = set()
    roots = [base_dir]
    if not skip_saved_scrapes and downloads_root.exists():
        roots.append(downloads_root)

    for root in roots:
        for path in sorted(root.rglob("*.csv")):
            resolved = path.resolve()
            if resolved in seen or not path.is_file():
                continue
            seen.add(resolved)
            if not looks_like_download_csv(path):
                continue
            yield infer_context(path, base_dir, downloads_root, vimm_metadata)


def normalize_row(row: dict[str, str], context: SourceContext) -> dict[str, str | int]:
    size_bytes = parse_int(row.get("size_bytes"))
    system_name = (
        normalize_text(row.get("system_name"))
        or normalize_text(row.get("platform_name"))
        or context.inferred_system_name
    )
    base_type = (
        normalize_text(row.get("base_type"))
        or normalize_text(row.get("normalized_type"))
        or context.inferred_base_type
    )
    payload: dict[str, str | int] = {
        "system_name": system_name,
        "base_type": base_type,
        "filename": normalize_text(row.get("filename")),
        "size_bytes": size_bytes,
        "size_human": normalize_text(row.get("size_human")) or (human_size(size_bytes) if size_bytes else ""),
        "method": normalize_text(row.get("method")),
        "final_url": normalize_text(row.get("final_url")) or normalize_text(row.get("url")),
        "source_page": normalize_text(row.get("source_page")),
        "request_data": normalize_text(row.get("request_data")),
        "content_type": normalize_text(row.get("content_type")),
        "status_code": normalize_text(row.get("status_code")),
        "reason": normalize_text(row.get("reason")),
        "anchor_text": normalize_text(row.get("anchor_text")),
        "error_message": normalize_text(row.get("error_message")),
        "site_domain": extract_domain(row.get("source_page", ""), row.get("final_url", ""), row.get("url", "")),
        "dedupe_key": "",
    }
    payload["dedupe_key"] = dedupe_key(payload)
    return payload


def choose_value(current: object, incoming: object) -> object:
    current_text = normalize_text(current)
    incoming_text = normalize_text(incoming)
    if not current_text and incoming_text:
        return incoming
    return current


def initialize_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(
        """
        CREATE TABLE schema_info (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE sources (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            source_type TEXT NOT NULL,
            label TEXT NOT NULL,
            file_size_bytes INTEGER NOT NULL,
            file_mtime TEXT NOT NULL,
            imported_at TEXT NOT NULL,
            row_count INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE downloads (
            id INTEGER PRIMARY KEY,
            dedupe_key TEXT NOT NULL UNIQUE,
            system_name TEXT NOT NULL DEFAULT '',
            base_type TEXT NOT NULL DEFAULT '',
            filename TEXT NOT NULL DEFAULT '',
            size_bytes INTEGER NOT NULL DEFAULT 0,
            size_human TEXT NOT NULL DEFAULT '',
            method TEXT NOT NULL DEFAULT '',
            final_url TEXT NOT NULL DEFAULT '',
            source_page TEXT NOT NULL DEFAULT '',
            request_data TEXT NOT NULL DEFAULT '',
            content_type TEXT NOT NULL DEFAULT '',
            status_code TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            anchor_text TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            site_domain TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        );

        CREATE TABLE observations (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            download_id INTEGER NOT NULL REFERENCES downloads(id) ON DELETE CASCADE,
            row_number INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            raw_json TEXT NOT NULL,
            UNIQUE(source_id, row_number)
        );

        CREATE INDEX downloads_system_name_idx ON downloads(system_name);
        CREATE INDEX downloads_site_domain_idx ON downloads(site_domain);
        CREATE INDEX downloads_size_bytes_idx ON downloads(size_bytes DESC);
        CREATE INDEX observations_download_id_idx ON observations(download_id);

        CREATE VIRTUAL TABLE download_search USING fts5(
            filename,
            system_name,
            base_type,
            site_domain,
            source_page,
            final_url,
            content='',
            tokenize='unicode61'
        );
        """
    )
    conn.execute(
        "INSERT INTO schema_info(key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )


def upsert_download(conn: sqlite3.Connection, payload: dict[str, str | int], observed_at: str) -> int:
    existing = conn.execute(
        """
        SELECT id, system_name, base_type, filename, size_bytes, size_human, method,
               final_url, source_page, request_data, content_type, status_code,
               reason, anchor_text, error_message, site_domain, first_seen_at, last_seen_at
        FROM downloads
        WHERE dedupe_key = ?
        """,
        (payload["dedupe_key"],),
    ).fetchone()

    if existing is None:
        cursor = conn.execute(
            """
            INSERT INTO downloads(
                dedupe_key, system_name, base_type, filename, size_bytes, size_human, method,
                final_url, source_page, request_data, content_type, status_code,
                reason, anchor_text, error_message, site_domain, first_seen_at, last_seen_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                payload["dedupe_key"],
                payload["system_name"],
                payload["base_type"],
                payload["filename"],
                payload["size_bytes"],
                payload["size_human"],
                payload["method"],
                payload["final_url"],
                payload["source_page"],
                payload["request_data"],
                payload["content_type"],
                payload["status_code"],
                payload["reason"],
                payload["anchor_text"],
                payload["error_message"],
                payload["site_domain"],
                observed_at,
                observed_at,
            ),
        )
        return int(cursor.lastrowid)

    current = dict(existing)
    merged = {
        "system_name": choose_value(current["system_name"], payload["system_name"]),
        "base_type": choose_value(current["base_type"], payload["base_type"]),
        "filename": choose_value(current["filename"], payload["filename"]),
        "size_bytes": max(int(current["size_bytes"]), int(payload["size_bytes"])),
        "size_human": choose_value(current["size_human"], payload["size_human"]),
        "method": choose_value(current["method"], payload["method"]),
        "final_url": choose_value(current["final_url"], payload["final_url"]),
        "source_page": choose_value(current["source_page"], payload["source_page"]),
        "request_data": choose_value(current["request_data"], payload["request_data"]),
        "content_type": choose_value(current["content_type"], payload["content_type"]),
        "status_code": choose_value(current["status_code"], payload["status_code"]),
        "reason": choose_value(current["reason"], payload["reason"]),
        "anchor_text": choose_value(current["anchor_text"], payload["anchor_text"]),
        "error_message": choose_value(current["error_message"], payload["error_message"]),
        "site_domain": choose_value(current["site_domain"], payload["site_domain"]),
    }
    if not normalize_text(merged["size_human"]) and int(merged["size_bytes"]):
        merged["size_human"] = human_size(int(merged["size_bytes"]))
    conn.execute(
        """
        UPDATE downloads
        SET system_name = ?, base_type = ?, filename = ?, size_bytes = ?, size_human = ?,
            method = ?, final_url = ?, source_page = ?, request_data = ?, content_type = ?,
            status_code = ?, reason = ?, anchor_text = ?, error_message = ?, site_domain = ?,
            last_seen_at = ?
        WHERE id = ?
        """,
        (
            merged["system_name"],
            merged["base_type"],
            merged["filename"],
            merged["size_bytes"],
            merged["size_human"],
            merged["method"],
            merged["final_url"],
            merged["source_page"],
            merged["request_data"],
            merged["content_type"],
            merged["status_code"],
            merged["reason"],
            merged["anchor_text"],
            merged["error_message"],
            merged["site_domain"],
            observed_at,
            current["id"],
        ),
    )
    return int(current["id"])


def rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM download_search")
    conn.execute(
        """
        INSERT INTO download_search(rowid, filename, system_name, base_type, site_domain, source_page, final_url)
        SELECT id, filename, system_name, base_type, site_domain, source_page, final_url
        FROM downloads
        """
    )


def build_db(base_dir: Path, downloads_root: Path, db_path: Path, skip_saved_scrapes: bool) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    initialize_db(conn)

    imported_files = 0
    imported_rows = 0
    observed_at = now_iso()
    with conn:
        for context in iter_candidate_csvs(base_dir, downloads_root, skip_saved_scrapes):
            stat = context.path.stat()
            source_cursor = conn.execute(
                """
                INSERT INTO sources(path, source_type, label, file_size_bytes, file_mtime, imported_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    str(context.path),
                    context.source_type,
                    context.label,
                    stat.st_size,
                    datetime.fromtimestamp(stat.st_mtime, timezone.utc).replace(microsecond=0).isoformat(),
                    observed_at,
                ),
            )
            source_id = int(source_cursor.lastrowid)
            row_count = 0

            with context.path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                for row_number, row in enumerate(reader, start=2):
                    payload = normalize_row(row, context)
                    if not normalize_text(payload["filename"]) and not normalize_text(payload["final_url"]):
                        continue
                    download_id = upsert_download(conn, payload, observed_at)
                    conn.execute(
                        """
                        INSERT INTO observations(source_id, download_id, row_number, observed_at, raw_json)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            source_id,
                            download_id,
                            row_number,
                            observed_at,
                            json.dumps(row, ensure_ascii=True, sort_keys=True),
                        ),
                    )
                    row_count += 1
            conn.execute(
                "UPDATE sources SET row_count = ? WHERE id = ?",
                (row_count, source_id),
            )
            imported_files += 1
            imported_rows += row_count

        rebuild_fts(conn)

    print(f"Database: {db_path}")
    print(f"Imported files: {imported_files}")
    print(f"Imported observations: {imported_rows}")
    print(
        "Unique downloads: "
        f"{conn.execute('SELECT COUNT(*) FROM downloads').fetchone()[0]}"
    )
    tracked_domains = conn.execute(
        "SELECT COUNT(DISTINCT site_domain) FROM downloads WHERE site_domain != ''"
    ).fetchone()[0]
    print(
        "Tracked domains: "
        f"{tracked_domains}"
    )
    conn.close()
    return 0


def print_stats(db_path: Path) -> int:
    if not db_path.exists():
        print(f"Missing database: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    totals = conn.execute(
        """
        SELECT
            COUNT(*) AS unique_downloads,
            COUNT(DISTINCT CASE WHEN system_name != '' THEN system_name END) AS systems,
            COUNT(DISTINCT CASE WHEN site_domain != '' THEN site_domain END) AS domains,
            SUM(size_bytes) AS total_bytes
        FROM downloads
        """
    ).fetchone()
    source_totals = conn.execute(
        """
        SELECT
            COUNT(*) AS source_files,
            SUM(row_count) AS source_rows
        FROM sources
        """
    ).fetchone()
    print(f"Database: {db_path}")
    print(f"Source files: {source_totals[0] or 0}")
    print(f"Observed rows: {source_totals[1] or 0}")
    print(f"Unique downloads: {totals[0] or 0}")
    print(f"Systems: {totals[1] or 0}")
    print(f"Domains: {totals[2] or 0}")
    print(f"Total indexed size: {human_size(int(totals[3] or 0))}")
    print()
    print("Top domains:")
    for row in conn.execute(
        """
        SELECT site_domain, COUNT(*) AS downloads, SUM(size_bytes) AS total_bytes
        FROM downloads
        WHERE site_domain != ''
        GROUP BY site_domain
        ORDER BY downloads DESC, total_bytes DESC
        LIMIT 10
        """
    ):
        print(f"- {row[0]}: {row[1]} downloads, {human_size(int(row[2] or 0))}")
    conn.close()
    return 0


def print_search_results(db_path: Path, query: str, limit: int) -> int:
    if not db_path.exists():
        print(f"Missing database: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                d.id,
                d.system_name,
                d.filename,
                d.site_domain,
                d.size_bytes,
                d.size_human,
                d.method,
                d.final_url,
                d.source_page,
                COUNT(o.id) AS observations
            FROM download_search s
            JOIN downloads d ON d.id = s.rowid
            LEFT JOIN observations o ON o.download_id = d.id
            WHERE download_search MATCH ?
            GROUP BY d.id
            ORDER BY bm25(download_search), d.size_bytes DESC
            LIMIT ?
            """,
            (query, max(1, limit)),
        ).fetchall()
    except sqlite3.OperationalError:
        like_query = f"%{query}%"
        rows = conn.execute(
            """
            SELECT
                d.id,
                d.system_name,
                d.filename,
                d.site_domain,
                d.size_bytes,
                d.size_human,
                d.method,
                d.final_url,
                d.source_page,
                COUNT(o.id) AS observations
            FROM downloads d
            LEFT JOIN observations o ON o.download_id = d.id
            WHERE d.filename LIKE ?
               OR d.system_name LIKE ?
               OR d.source_page LIKE ?
               OR d.final_url LIKE ?
               OR d.site_domain LIKE ?
            GROUP BY d.id
            ORDER BY d.size_bytes DESC, d.filename ASC
            LIMIT ?
            """,
            (like_query, like_query, like_query, like_query, like_query, max(1, limit)),
        ).fetchall()
    if not rows:
        print("No matches.")
        conn.close()
        return 0

    for row in rows:
        system_text = row["system_name"] or "Unknown system"
        domain_text = row["site_domain"] or "unknown-domain"
        size_text = row["size_human"] or human_size(int(row["size_bytes"] or 0))
        print(
            f"- [{row['id']}] {row['filename']} | {system_text} | {domain_text} | "
            f"{size_text} | seen {row['observations']} time(s)"
        )
        print(f"  method={row['method']} final_url={row['final_url']}")
        if row["source_page"]:
            print(f"  source_page={row['source_page']}")
    conn.close()
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "build":
        base_dir = Path(args.base_dir).expanduser().resolve()
        downloads_root = Path(args.downloads_root).expanduser().resolve()
        db_path = (
            Path(args.db).expanduser().resolve()
            if args.db
            else (base_dir / DEFAULT_DB_NAME)
        )
        return build_db(base_dir, downloads_root, db_path, args.skip_saved_scrapes)

    if args.command == "stats":
        return print_stats(Path(args.db).expanduser().resolve())

    return print_search_results(
        Path(args.db).expanduser().resolve(),
        args.query,
        args.limit,
    )


if __name__ == "__main__":
    raise SystemExit(main())
