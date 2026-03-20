# DownloadScapper

`DownloadScapper` is a local Python toolkit for:

- crawling sites and discovering downloadable files
- reviewing/exporting structured download reports
- running a browser UI for CSV-driven downloads
- building a local SQLite index for discovered download links

## Requirements

- Python 3.11+
- Optional: [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) for direct media URL downloads in the UI

Most of the project uses the Python standard library. There is no required Python package install step for the core scripts.

## Documentation

- [Command Reference](docs/commands.md)
- [Local Workspace](docs/local-workspace.md)
- [Release Checklist](docs/release-checklist.md)

## Generic Release Layout

The repository is meant to stay generic and code-only.

Generated outputs are written into an ignored local workspace by default:

```text
.local-workspace/
```

That workspace is where Vimm exports, crawl reports, SQLite indexes, and other machine-specific data should live. It is excluded from Git by `.gitignore`.

## Quick Start

From the repo root:

```bash
python3 downloadscapper.py doctor
python3 downloadscapper.py ui
```

The UI runs on:

```text
http://127.0.0.1:8765
```

The launcher opens a browser tab by default. Use `python3 downloadscapper.py ui -- --no-browser` to start the server without opening a tab.

## Main Commands

Run a project and workspace sanity check:

```bash
python3 downloadscapper.py doctor
```

Launch the browser UI:

```bash
python3 downloadscapper.py ui
```

Rebuild the Vimm exports inside `.local-workspace/`:

```bash
python3 downloadscapper.py rebuild-vimm
```

Crawl Vimm first, then rebuild the local exports:

```bash
python3 downloadscapper.py rebuild-vimm --crawl-first --skip-existing --insecure
```

Build the local SQLite download index:

```bash
python3 downloadscapper.py downloads-db build
```

Show download index stats:

```bash
python3 downloadscapper.py downloads-db stats
```

Search the indexed downloads:

```bash
python3 downloadscapper.py downloads-db search "playstation"
```

Run the standalone crawler directly:

```bash
python3 website_download_summary.py https://example.com --show-links
```

For full command details and examples, see [Command Reference](docs/commands.md).

## UI Capabilities

The browser client supports:

- discovery from a live URL
- continuing from a previously saved scrape CSV
- direct media URL downloads through `yt-dlp`
- CSV import, preview, and column mapping
- `GET` and `POST` downloads
- collision handling and optional subfolders
- persisted session history and job manifests

By default, scrape archives and UI state are written outside the repo under:

- `~/Downloads/downloadscapper-scrapes/`
- `~/Downloads/downloadscapper-state/`

## Repository Layout

- `downloadscapper.py`: single entry point for the toolkit
- `website_download_summary.py`: crawler and discovery engine
- `csv_download_client.py`: local HTTP server and browser UI
- `crawl_vimm_disc_systems.py`: batch Vimm crawler
- `build_vimm_master_deduped.py`: master deduped dataset builder
- `build_vimm_exports.py`: derived report builder
- `download_index.py`: SQLite index builder and search CLI
- `download_client_ui/`: browser UI assets
- `.local-workspace/`: ignored local output area for generated data

## Notes For GitHub

- Keep source code and UI assets in the repo.
- Keep private or generated CSV/JSON/SQLite output in `.local-workspace/`.
- If you need a shareable example dataset, create a small sanitized sample instead of committing personal working data.
