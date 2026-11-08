# Contributing

Thanks for improving the NSE Dividend Tracker. This project is small, local-first,
and deliberately dependency-light.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt -r requirements-dev.txt
python scripts/check_setup.py
python -m pytest
```

Start the app with `python server.py` or `python run.py --no-venv`.

## Before you open a pull request

```bash
python scripts/preflight.py
```

That runs the same gate as the Git hooks:

```bash
python -m ruff check .
python -m pytest
python scripts/smoke_test.py
python scripts/check_repo.py
python scripts/check_docs.py
python scripts/check_ci.py      # latest CI run on the current branch must be green
```

All six must pass. Install the hooks once per clone with
`python scripts/install_hooks.py`; after that `git commit` runs lint and tests,
and `git push` also runs the smoke test, privacy audit, documentation check and
CI status check. CI also runs on Windows and macOS, so avoid POSIX-only paths,
shell assumptions, or case-sensitive filenames.

## Code guidelines

- Keep the backend dependency set small. The runtime is Flask, ChromaDB, and
  python-dotenv; discuss new dependencies first.
- Use `pathlib`/`os.path` instead of hard-coded `/` separators.
- Keep storage access inside `db.py`; routes should not talk to ChromaDB
  directly.
- Every write must bump the revision counter and update the JSON mirror.
- Preserve the delete tombstone rules: a stale write must never resurrect a
  deleted holding; a genuinely new add (newer `updatedAt`) may.
- Use temporary `DIVIDEND_DB_DIR` and `DIVIDEND_DATA_DIR` in tests. Never point
  tests at real user data.
- Mock `market._fetch_html` or `server.get_dividends` in tests; do not depend on
  the network.
- Keep `templates/index.html`, `static/app.css` and `static/app.js`
  dependency-free (vanilla HTML/CSS/JS).

## Privacy rules

Never commit:

- `data/portfolio.json` or anything else under `data/` except `.gitkeep`
- `chroma_db/`
- `.env` or credentials
- personal HTML exports, broker statements, or screenshots with real holdings

`python scripts/check_repo.py` enforces these rules and checks required files.

## Commit style

Use short imperative subjects, for example:

```text
Add quarterly import validation
Fix stale-window delete resurrection
Document ChromaDB backup and restore
```

Group related changes in one commit. Do not mix formatting-only churn with
behavior changes.

## Reporting bugs

Include:

- operating system and Python version
- the command you ran
- the full error message
- whether the issue reproduces with a fresh temporary database

Do not attach your real `portfolio.json`, `chroma_db/`, or screenshots that show
account details.

## License

By contributing, you agree that your contributions are licensed under the
[MIT License](LICENSE).
