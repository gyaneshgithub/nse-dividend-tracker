#!/usr/bin/env python3
"""Point Git at the tracked hooks in `.githooks/`.

Hooks are local Git configuration, so every clone runs this once:

    python scripts/install_hooks.py

Remove them again with:

    git config --unset core.hooksPath
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS_DIR = ROOT / ".githooks"


def main() -> int:
    if not (ROOT / ".git").exists():
        print("Not a Git repository: " + str(ROOT), file=sys.stderr)
        return 1
    if not HOOKS_DIR.is_dir():
        print("Missing hook directory: " + str(HOOKS_DIR), file=sys.stderr)
        return 1

    result = subprocess.run(
        ["git", "config", "core.hooksPath", ".githooks"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr.strip() or "git config failed", file=sys.stderr)
        return 1

    # Git for Windows runs hooks through its bundled shell and does not need
    # the executable bit, but macOS and Linux do.
    for hook in sorted(HOOKS_DIR.iterdir()):
        if not hook.is_file():
            continue
        mode = hook.stat().st_mode
        os.chmod(hook, mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        print(f"  ready  {hook.relative_to(ROOT)}")

    print("\nGit will now run these hooks:")
    print("  pre-commit  -> ruff + pytest")
    print("  pre-push    -> ruff + pytest + smoke test + repository audit + docs")
    print("Bypass a single run with --no-verify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
