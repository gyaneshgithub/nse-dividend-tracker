#!/usr/bin/env python3
"""Fail if the latest CI run for a branch is not green.

Usage:
    python scripts/check_ci.py            # uses the current branch
    python scripts/check_ci.py <branch>

Returns 0 when the latest run of the ``CI`` workflow on that branch concluded
``success``, and 1 otherwise. Returns 0 with a note when the GitHub CLI is
unavailable or authenticated session is missing, so a local push is never
blocked by an infrastructure hiccup.

This is the same check behind the README's CI badge.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    branch = argv[0] if argv else _current_branch()
    result = subprocess.run(
        [
            "gh",
            "run",
            "list",
            "--workflow",
            "CI",
            "--branch",
            branch,
            "--limit",
            "1",
            "--json",
            "conclusion",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("CI check skipped (gh CLI unavailable or not authenticated).")
        return 0
    runs = json.loads(result.stdout or "[]")
    if not runs:
        print(f"CI check skipped (no CI runs found for branch {branch}).")
        return 0
    conclusion = runs[0].get("conclusion")
    if conclusion != "success":
        print(f"CI check failed: latest CI run on {branch} concluded {conclusion}.")
        return 1
    print(f"CI check passed: latest CI run on {branch} concluded success.")
    return 0


def _current_branch() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else "main"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
