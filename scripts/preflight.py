#!/usr/bin/env python3
"""Run the checks that gate commits and pushes.

`scripts/install_hooks.py` connects Git to `.githooks/`, which calls this
script. Running it by hand is the same gate:

    python scripts/preflight.py              # full gate, same as a push
    python scripts/preflight.py --stage commit

Exit code 0 means every check passed, 1 means at least one failed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _steps(stage: str):
    """Return the checks for a stage, in the order CI runs them."""
    python = sys.executable
    branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip() or "main"
    steps = [
        ("Lint (ruff)", [python, "-m", "ruff", "check", "."], "commit"),
        ("Tests (pytest)", [python, "-m", "pytest", "-q"], "commit"),
    ]
    if stage == "push":
        steps += [
            ("Smoke test", [python, "scripts/smoke_test.py"], "push"),
            ("Repository audit", [python, "scripts/check_repo.py"], "push"),
            ("Documentation check", [python, "scripts/check_docs.py"], "push"),
            ("CI status", [python, "scripts/check_ci.py", branch], "push"),
        ]
    return steps


def _run(step):
    name, command, _stage = step
    started = time.monotonic()
    print(f"  ... {name}", flush=True)
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    elapsed = time.monotonic() - started
    if result.returncode == 0:
        print(f"  ok  {name} ({elapsed:.1f}s)", flush=True)
        return True
    print(f"  FAIL {name} ({elapsed:.1f}s)", flush=True)
    output = (result.stdout or "") + (result.stderr or "")
    if output.strip():
        print(output.rstrip(), file=sys.stderr, flush=True)
    return False


def check_ci(branch: str) -> tuple[bool, str]:
    """Ask the GitHub CLI for the latest run of the CI workflow on this branch.

    Returns (ok, note). ok is False when the latest run concluded with a
    failure. When the CLI is unavailable or unauthenticated we skip the check
    with a warning rather than blocking a local push.
    """
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
        return True, "CI check skipped (gh CLI unavailable or not authenticated)"
    runs = json.loads(result.stdout or "[]")
    if not runs:
        return True, "CI check skipped (no CI runs found for this branch)"
    conclusion = runs[0].get("conclusion")
    if conclusion != "success":
        return False, f"CI latest run concluded: {conclusion}"
    return True, f"CI latest run on {branch}: success"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        choices=("commit", "push"),
        default="push",
        help="commit runs lint and tests; push also runs smoke and privacy checks",
    )
    args = parser.parse_args(argv)

    steps = _steps(args.stage)
    print(f"Preflight ({args.stage}): {len(steps)} checks", flush=True)
    failed = [step[0] for step in steps if not _run(step)]
    if failed:
        print(
            "\nPreflight failed: " + ", ".join(failed) + "\n"
            "Fix the failures, or bypass with --no-verify if it is truly an emergency.",
            file=sys.stderr,
        )
        return 1
    print(f"\nPreflight passed ({args.stage}).", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nPreflight cancelled.", file=sys.stderr)
        raise SystemExit(130)
