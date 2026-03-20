# Local Workspace

Generated data is kept out of Git by default in:

```text
.local-workspace/
```

This keeps the repository generic while still letting you keep large or private working data on disk.

## What Belongs Here

- Vimm crawl outputs such as `vimm-*-report.csv`, `vimm-*-report.json`, and `vimm-*-summary.txt`
- deduped and derived Vimm exports
- `vimm-system-csvs/`
- `vimm-systems/`
- local SQLite indexes such as `download-index.sqlite3`
- historical archive material under `archive/`

## Current Layout

Typical structure:

```text
.local-workspace/
  archive/
  download-index.sqlite3
  vimm-*.csv
  vimm-*.json
  vimm-*.txt
  vimm-system-csvs/
  vimm-systems/
```

## Why This Exists

- keeps personal data out of GitHub
- prevents accidental commits of large generated files
- makes the repository look like a software project instead of a private data dump
- gives the scripts one predictable place for local outputs

## Common Actions

Rebuild the local Vimm dataset:

```bash
python3 downloadscapper.py rebuild-vimm
```

Build the local SQLite index:

```bash
python3 downloadscapper.py downloads-db build
```

Inspect the workspace:

```bash
python3 downloadscapper.py doctor
```

## Sharing Data Safely

If you want to publish example outputs:

- create a small sanitized sample in a separate folder
- avoid committing full working datasets
- avoid committing machine-specific exports or databases
- prefer documentation and reproducible commands over checked-in personal output
