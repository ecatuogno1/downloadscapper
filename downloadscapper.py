#!/usr/bin/env python3

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from project_paths import default_project_dir, default_workspace_dir


PROJECT_DIR = default_project_dir()
WORKSPACE_DIR = default_workspace_dir()
SCRIPT_BY_COMMAND = {
    "ui": PROJECT_DIR / "csv_download_client.py",
    "crawl-vimm": PROJECT_DIR / "crawl_vimm_disc_systems.py",
    "build-master": PROJECT_DIR / "build_vimm_master_deduped.py",
    "build-exports": PROJECT_DIR / "build_vimm_exports.py",
    "enrich-vimm": PROJECT_DIR / "enrich_vimm_platforms.py",
    "downloads-db": PROJECT_DIR / "download_index.py",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Single entry point for the DownloadScapper tools."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check that the workspace has the expected scripts and folders.",
    )
    doctor_parser.add_argument(
        "--base-dir",
        default=str(WORKSPACE_DIR),
        help=f"Local data workspace to inspect. Default: {WORKSPACE_DIR}",
    )

    for command, help_text in (
        ("ui", "Launch the browser-based CSV download client."),
        ("crawl-vimm", "Run the Vimm disc-system crawler."),
        ("build-master", "Build the master deduped Vimm dataset."),
        ("build-exports", "Build the derived Vimm export files."),
        ("enrich-vimm", "Enrich a Vimm crawl report with platform labels."),
        ("downloads-db", "Build or query the indexed SQLite downloads database."),
    ):
        command_parser = subparsers.add_parser(command, help=help_text)
        command_parser.add_argument(
            "args",
            nargs=argparse.REMAINDER,
            help="Arguments forwarded to the underlying script.",
        )

    rebuild_parser = subparsers.add_parser(
        "rebuild-vimm",
        help="Run the common Vimm rebuild flow in sequence.",
    )
    rebuild_parser.add_argument(
        "--base-dir",
        default=str(WORKSPACE_DIR),
        help=f"Local data workspace to read/write reports from. Default: {WORKSPACE_DIR}",
    )
    rebuild_parser.add_argument(
        "--crawl-first",
        action="store_true",
        help="Run the disc-system crawl before rebuilding master and exports.",
    )
    rebuild_parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="When used with --crawl-first, reuse existing JSON reports when present.",
    )
    rebuild_parser.add_argument(
        "--insecure",
        action="store_true",
        help="When used with --crawl-first, disable TLS verification in the crawler.",
    )

    return parser


def forward_args(args: list[str]) -> list[str]:
    if args and args[0] == "--":
        return args[1:]
    return args


def run_script(script_path: Path, extra_args: list[str]) -> int:
    cmd = [sys.executable, str(script_path), *forward_args(extra_args)]
    try:
        completed = subprocess.run(cmd)
    except KeyboardInterrupt:
        return 130
    return completed.returncode


def run_step(label: str, script_path: Path, extra_args: list[str]) -> None:
    print(f"==> {label}", flush=True)
    cmd = [sys.executable, str(script_path), *extra_args]
    subprocess.run(cmd, check=True)


def run_doctor(base_dir: Path) -> int:
    required_paths = [
        PROJECT_DIR / "downloadscapper.py",
        PROJECT_DIR / "csv_download_client.py",
        PROJECT_DIR / "crawl_vimm_disc_systems.py",
        PROJECT_DIR / "build_vimm_master_deduped.py",
        PROJECT_DIR / "build_vimm_exports.py",
        PROJECT_DIR / "download_index.py",
        PROJECT_DIR / "website_download_summary.py",
        PROJECT_DIR / "download_client_ui" / "index.html",
        PROJECT_DIR / "download_client_ui" / "app.js",
        PROJECT_DIR / "download_client_ui" / "styles.css",
    ]

    print(f"Project: {PROJECT_DIR}")
    print(f"Workspace: {base_dir}")
    print(f"Python: {sys.executable}")
    print(f"Launcher project dir: {PROJECT_DIR}")

    missing = [path for path in required_paths if not path.exists()]
    if missing:
        print("Missing required files:")
        for path in missing:
            print(f"- {path}")
        return 1

    print("Data snapshot:")
    for path in (
        base_dir / "vimm-master-deduped.csv",
        base_dir / "vimm-systems" / "index.csv",
        base_dir / "download-index.sqlite3",
    ):
        status = "present" if path.exists() else "missing"
        print(f"- {path.relative_to(base_dir)}: {status}")

    print("Doctor check passed.")
    return 0


def run_rebuild_vimm(args: argparse.Namespace) -> int:
    base_dir = Path(args.base_dir).expanduser().resolve()
    base_dir.mkdir(parents=True, exist_ok=True)
    try:
        if args.crawl_first:
            crawl_args = ["--base-dir", str(base_dir)]
            if args.skip_existing:
                crawl_args.append("--skip-existing")
            if args.insecure:
                crawl_args.append("--insecure")
            run_step("crawl-vimm", SCRIPT_BY_COMMAND["crawl-vimm"], crawl_args)

        run_step(
            "build-master",
            SCRIPT_BY_COMMAND["build-master"],
            ["--base-dir", str(base_dir)],
        )
        run_step(
            "build-exports",
            SCRIPT_BY_COMMAND["build-exports"],
            ["--base-dir", str(base_dir)],
        )
    except subprocess.CalledProcessError as exc:
        return exc.returncode
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return run_doctor(Path(args.base_dir).expanduser().resolve())

    if args.command == "rebuild-vimm":
        return run_rebuild_vimm(args)

    script_path = SCRIPT_BY_COMMAND[args.command]
    return run_script(script_path, args.args)


if __name__ == "__main__":
    raise SystemExit(main())
