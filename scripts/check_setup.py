#!/usr/bin/env python3
"""Verify that the local installation can run the Dividend Tracker.

Checks Python, dependencies, required files, writable data directories and a
throwaway ChromaDB database. It never opens or modifies your real database.
"""

from __future__ import annotations

import importlib.metadata
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass


def _ok(message: str) -> None:
    print(f"  OK   {message}")


def _fail(message: str) -> None:
    print(f"  FAIL {message}")


def main() -> int:
    failures = 0
    print("NSE Dividend Tracker setup check")
    print(f"Project: {ROOT}")

    version = sys.version_info
    if version[:2] < (3, 11):
        _fail(f"Python 3.11+ required (found {sys.version.split()[0]})")
        failures += 1
    elif version[:2] > (3, 13):
        print(
            f"  WARN Python {sys.version.split()[0]} is newer than the tested "
            "range (3.11-3.13)"
        )
    else:
        _ok(f"Python {sys.version.split()[0]}")

    modules = {
        "flask": ("Flask", "flask"),
        "chromadb": ("ChromaDB", "chromadb"),
        "dotenv": ("python-dotenv", "python-dotenv"),
    }
    imported = {}
    for module_name, (label, package) in modules.items():
        try:
            imported[module_name] = __import__(module_name)
            try:
                version_text = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                version_text = ""
            _ok(f"{label} {version_text}".strip())
        except Exception as exc:
            _fail(f"{label} is not available: {exc}")
            failures += 1

    required = [
        "server.py",
        "db.py",
        "market.py",
        "run.py",
        "requirements.txt",
        "templates/index.html",
        "static/app.css",
        "static/app.js",
        "routes/__init__.py",
    ]
    for relative in required:
        path = ROOT / relative
        if path.exists():
            _ok(relative)
        else:
            _fail(f"missing {relative}")
            failures += 1

    data_dir = Path(os.environ.get("DIVIDEND_DATA_DIR", ROOT / "data"))
    if not data_dir.is_absolute():
        data_dir = ROOT / data_dir
    db_dir = Path(os.environ.get("DIVIDEND_DB_DIR", ROOT / "chroma_db"))
    if not db_dir.is_absolute():
        db_dir = ROOT / db_dir
    print(f"Data directory: {data_dir}")
    print(f"Database directory: {db_dir}")

    for label, directory in (("data", data_dir), ("database", db_dir)):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".setup-check.tmp"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            _ok(f"{label} directory is writable")
        except Exception as exc:
            _fail(f"{label} directory is not writable: {exc}")
            failures += 1

    if "chromadb" in imported:
        try:
            with tempfile.TemporaryDirectory(prefix="dividend-chroma-") as tmp:
                client = imported["chromadb"].PersistentClient(path=tmp)
                try:
                    collection = client.get_or_create_collection("setup_check")
                    collection.add(
                        ids=["probe"],
                        documents=["setup probe"],
                        embeddings=[[0.0] * 384],
                    )
                    if collection.count() != 1:
                        raise RuntimeError("unexpected collection count")
                finally:
                    # Windows keeps the SQLite files locked until the client
                    # stops, which would make TemporaryDirectory cleanup fail.
                    client.close()
            _ok("ChromaDB can create and read a local database")
        except Exception as exc:
            _fail(f"ChromaDB self-test failed: {exc}")
            failures += 1

    print()
    if failures:
        print(f"{failures} check(s) failed. See docs/SETUP.md for help.")
        return 1
    print("All checks passed. Start the app with: python run.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
