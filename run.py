#!/usr/bin/env python3
"""Universal launcher for the NSE Dividend Tracker.

On first run this creates a local ``.venv``, installs the pinned
requirements, and starts the Flask server. Later runs reuse the environment.

Usage:
    python run.py                 # bootstrap if needed, then start
    python run.py --no-venv       # use the current Python (CI / Docker)
    python run.py --skip-install  # never run pip
    python run.py --host 0.0.0.0 --port 9000
    python run.py --version
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV_DIR / ".requirements.sha256"
MIN_PYTHON = (3, 11)
TESTED_MAX = (3, 13)


def venv_python(venv_dir: Path = VENV_DIR) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def in_virtualenv() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def requirements_hash() -> str:
    digest = hashlib.sha256()
    digest.update(REQUIREMENTS.read_bytes())
    return digest.hexdigest()


def check_python() -> None:
    if sys.version_info[:2] < MIN_PYTHON:
        sys.exit(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required; "
            f"found {sys.version.split()[0]}."
        )
    if sys.version_info[:2] > TESTED_MAX:
        print(
            f"Warning: Python {sys.version.split()[0]} is newer than the tested "
            f"range ({MIN_PYTHON[0]}.{MIN_PYTHON[1]}-{TESTED_MAX[0]}.{TESTED_MAX[1]}). "
            "If installation fails, install Python 3.12 and rerun."
        )


def install_requirements(python: Path) -> None:
    print("[setup] Installing dependencies (this can take a few minutes)...")
    subprocess.check_call(
        [str(python), "-m", "pip", "install", "--upgrade", "pip", "--quiet"]
    )
    subprocess.check_call(
        [str(python), "-m", "pip", "install", "-r", str(REQUIREMENTS)]
    )
    STAMP.write_text(requirements_hash(), encoding="utf-8")
    print("[setup] Dependencies installed.")


def ensure_venv(skip_install: bool) -> Path:
    python = venv_python()
    if not python.exists():
        print(f"[setup] Creating virtual environment at {VENV_DIR}")
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)
        python = venv_python()

    if not skip_install:
        stamp = STAMP.read_text(encoding="utf-8").strip() if STAMP.exists() else ""
        if stamp != requirements_hash():
            install_requirements(python)

    return python


def start_server() -> None:
    import server

    server.main()


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the NSE Dividend Tracker.")
    parser.add_argument(
        "--no-venv",
        action="store_true",
        help="run with the current Python instead of creating/using .venv",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="do not run pip even when requirements changed",
    )
    parser.add_argument("--host", help="bind address (default: HOST or 127.0.0.1)")
    parser.add_argument("--port", help="port (default: PORT or 8901)")
    parser.add_argument("--version", action="store_true", help="print version and exit")
    args = parser.parse_args()

    if args.version:
        print("NSE Dividend Tracker 1.1.0")
        return

    if args.host:
        os.environ["HOST"] = args.host
    if args.port:
        os.environ["PORT"] = str(args.port)

    check_python()

    no_venv = args.no_venv or os.environ.get("DIVIDEND_NO_VENV") == "1"
    if no_venv or in_virtualenv():
        start_server()
        return

    python = ensure_venv(skip_install=args.skip_install)
    # Hand over to the virtual environment. os.execv keeps Ctrl+C and exit
    # codes behaving like a single process on macOS, Linux and Windows.
    os.execv(str(python), [str(python), str(Path(__file__).resolve()), *sys.argv[1:]])


if __name__ == "__main__":
    main()
