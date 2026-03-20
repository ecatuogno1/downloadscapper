# Release Checklist

Use this before pushing the repo publicly.

## Repository

- Confirm the repo root contains source code, UI assets, and docs only.
- Confirm `.local-workspace/` is ignored.
- Confirm no personal CSV, JSON, TXT, or SQLite outputs are present outside `.local-workspace/`.
- Confirm no absolute home-directory paths remain in tracked files.

## Documentation

- Update [README.md](../README.md) if commands or defaults changed.
- Keep [docs/commands.md](commands.md) aligned with `--help`.
- Keep [docs/local-workspace.md](local-workspace.md) aligned with actual output paths.

## Verification

Run:

```bash
python3 downloadscapper.py doctor
python3 downloadscapper.py --help
python3 download_index.py --help
python3 csv_download_client.py --help
```

If you changed the Vimm flow, also run:

```bash
python3 downloadscapper.py rebuild-vimm
```

## Git Check

Run:

```bash
git status --short --ignored
```

Expected outcome:

- source files and docs appear as tracked or ready-to-stage changes
- `.local-workspace/` appears as ignored
- no generated personal data appears as tracked content

## Optional Public-Repo Extras

- add a license file
- add a small sanitized sample dataset if needed
- add screenshots or a short demo GIF for the UI
- add issue templates or contribution notes if you want outside contributors
