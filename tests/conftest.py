"""Shared pytest fixtures. Every test gets throwaway storage."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("DIVIDEND_CACHE_TTL", "0")


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Point db.py at a temporary database and data directory."""
    import db as database

    monkeypatch.setattr(database, "DB_DIR", str(tmp_path / "chroma"))
    monkeypatch.setattr(database, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(database, "BACKUP_FILE", str(tmp_path / "data" / "portfolio.json"))
    database.reset_connection()
    yield database
    database.reset_connection()


@pytest.fixture
def client(db):
    import server

    server.app.config.update(TESTING=True)
    return server.app.test_client()
