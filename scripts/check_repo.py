#!/usr/bin/env python3
"""Privacy and packaging audit for the repository.

Fails when private portfolio data, local databases, secrets or personal HTML
files are tracked by git. Run it before every push:

    python scripts/check_repo.py
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_FILES = {
    "data/portfolio.json",
    ".env",
    "index.html",
    "portfolio.html",
    "dividend-tracker.html",
}
FORBIDDEN_PREFIXES = (
    "chroma_db/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "data/",
)
FORBIDDEN_SUFFIXES = (".sqlite3", ".sqlite3-shm", ".sqlite3-wal", ".log", ".pid")
ALLOWED_DATA_FILES = {"data/.gitkeep"}
REQUIRED_FILES = {
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "AGENTS.md",
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "server.py",
    "db.py",
    "market.py",
    "run.py",
    "start.sh",
    "start.bat",
    "templates/index.html",
    "docs/SETUP.md",
    "docs/USER_GUIDE.md",
    "docs/CHROMADB.md",
    "docs/ARCHITECTURE.md",
    "docs/images/tracker.png",
    "scripts/check_setup.py",
    "scripts/smoke_test.py",
    "scripts/check_repo.py",
    "Dockerfile",
    "docker-compose.yml",
    ".github/workflows/ci.yml",
}


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True)


def main() -> int:
    if not (ROOT / ".git").exists():
        print("Not a git repository yet. Run this after: git init")
        return 2

    tracked = [line for line in git("ls-files").splitlines() if line]
    problems = []

    for path in tracked:
        if path in ALLOWED_DATA_FILES:
            continue
        if path in FORBIDDEN_FILES:
            problems.append(f"private/local file is tracked: {path}")
        elif path.startswith(FORBIDDEN_PREFIXES):
            problems.append(f"local data directory is tracked: {path}")
        elif path.endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"local artifact is tracked: {path}")

    missing = sorted(REQUIRED_FILES - set(tracked))
    for path in missing:
        problems.append(f"required file is not tracked: {path}")

    for path in tracked:
        full = ROOT / path
        if full.is_file() and full.stat().st_size > 5 * 1024 * 1024:
            problems.append(f"file is larger than 5 MB: {path}")

    print(f"Tracked files: {len(tracked)}")
    if problems:
        print("\nRepository audit failed:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    print("Repository audit passed: no portfolio data, secrets or local files tracked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
