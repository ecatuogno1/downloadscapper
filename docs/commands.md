# Command Reference

This project has one main entry point:

```bash
python3 downloadscapper.py <command>
```

By default, generated data is written into:

```text
.local-workspace/
```

## Core Commands

### `doctor`

Checks that the code files exist and reports whether key local data artifacts are present.

```bash
python3 downloadscapper.py doctor
python3 downloadscapper.py doctor --base-dir /path/to/workspace
```

### `ui`

Starts the local browser app.

```bash
python3 downloadscapper.py ui
python3 downloadscapper.py ui -- --no-browser
python3 csv_download_client.py --host 127.0.0.1 --port 8765
```

Important flags on `csv_download_client.py`:

- `--output-dir`: default download destination
- `--scrape-save-dir`: archive location for discovery CSV/JSON files
- `--state-dir`: persistent UI job/session state
- `--timeout-seconds`: per-download timeout
- `--no-browser`: do not auto-open a tab

### `downloads-db`

Builds or queries the local SQLite index.

```bash
python3 downloadscapper.py downloads-db build
python3 downloadscapper.py downloads-db stats
python3 downloadscapper.py downloads-db search "playstation"
```

Useful direct forms:

```bash
python3 download_index.py build --skip-saved-scrapes
python3 download_index.py build --db .local-workspace/custom-index.sqlite3
python3 download_index.py search "xbox" --limit 50
```

The index defaults to `.local-workspace/download-index.sqlite3`.

## Crawling Commands

### Generic site crawl

```bash
python3 website_download_summary.py https://example.com --show-links
```

Useful flags:

- `--max-pages`: limit the crawl size
- `--workers`: parallel size-check workers
- `--timeout`: network timeout
- `--retries`: retry count for transient failures
- `--allow-subdomains`: include subdomains
- `--ignore-robots`: ignore `robots.txt`
- `--insecure`: disable TLS verification
- `--json-out`: save structured results
- `--csv-out`: save tabular results

Example:

```bash
python3 website_download_summary.py \
  https://example.com/downloads \
  --max-pages 100 \
  --workers 12 \
  --json-out .local-workspace/example-report.json \
  --csv-out .local-workspace/example-report.csv
```

### Vimm crawl

```bash
python3 downloadscapper.py crawl-vimm
python3 downloadscapper.py crawl-vimm -- --skip-existing
python3 downloadscapper.py crawl-vimm -- --systems PS1 PS2 Saturn
```

Direct form:

```bash
python3 crawl_vimm_disc_systems.py --skip-existing
```

Outputs land in `.local-workspace/` by default.

## Vimm Build Commands

### Build master dataset

```bash
python3 downloadscapper.py build-master
python3 build_vimm_master_deduped.py --base-dir .local-workspace
```

Default outputs:

- `.local-workspace/vimm-master-deduped.csv`
- `.local-workspace/vimm-master-deduped-summary.md`

### Build derived exports

```bash
python3 downloadscapper.py build-exports
python3 build_vimm_exports.py --base-dir .local-workspace
```

This creates:

- `vimm-console-totals.*`
- `vimm-top-100-largest.*`
- `vimm-download-links.csv`
- `vimm-system-csvs/`
- `vimm-systems/`

### Rebuild common Vimm flow

```bash
python3 downloadscapper.py rebuild-vimm
python3 downloadscapper.py rebuild-vimm --crawl-first --skip-existing
python3 downloadscapper.py rebuild-vimm --crawl-first --skip-existing --insecure
```

This runs:

1. `crawl-vimm` optionally
2. `build-master`
3. `build-exports`

## Root-Vault Enrichment

This is the older helper flow for the historical archive files under `.local-workspace/archive/`.

```bash
python3 enrich_vimm_platforms.py
```

Default inputs and outputs:

- input: `.local-workspace/archive/vimm-vault-report.json`
- output CSV: `.local-workspace/archive/vimm-vault-report-platforms.csv`
- output summary: `.local-workspace/archive/vimm-vault-platform-summary.md`
