#!/usr/bin/env python3
"""Check that the documentation still matches the shipped features.

Two cheap guarantees, both deterministic:

1. Every relative Markdown link points at a file that exists.
2. The README feature list still mentions the headline capabilities.

Run it directly or let `scripts/preflight.py` call it before a push.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOC_FILES = [
    ROOT / "README.md",
    ROOT / "CONTRIBUTING.md",
    ROOT / "CHANGELOG.md",
    ROOT / "AGENTS.md",
    ROOT / "docs" / "ARCHITECTURE.md",
    ROOT / "docs" / "CHROMADB.md",
    ROOT / "docs" / "SETUP.md",
    ROOT / "docs" / "USER_GUIDE.md",
]

# Feature names that must stay in the README's feature list. Update this list
# when a headline feature is added or renamed.
README_REQUIRED = [
    "Next-year income scenarios",
    "DPS CAGR",
    "Financial-year view",
    "Portfolio briefing",
    "Yield vs own history",
    "first run",
]

LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def check_links(path: Path) -> list[str]:
    problems = []
    text = path.read_text(encoding="utf-8")
    for target in LINK_RE.findall(text):
        if target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        clean = target.split("#", 1)[0]
        if not clean:
            continue
        resolved = (path.parent / clean).resolve()
        if not resolved.exists():
            problems.append(f"{path.relative_to(ROOT)}: missing link target {target}")
    return problems


def check_readme_features() -> list[str]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    features = readme.split("## Features", 1)[-1].split("\n## ", 1)[0]
    lowered = features.lower()
    missing = [name for name in README_REQUIRED if name.lower() not in lowered]
    return [f"README.md: feature list is missing {name!r}" for name in missing]


def main() -> int:
    problems = []
    for path in DOC_FILES:
        if not path.exists():
            problems.append(f"{path.relative_to(ROOT)}: file is missing")
            continue
        problems += check_links(path)
    problems += check_readme_features()

    if problems:
        print("Documentation check failed:", file=sys.stderr)
        for problem in problems:
            print("  - " + problem, file=sys.stderr)
        return 1
    print(f"Documentation check passed ({len(DOC_FILES)} files).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
