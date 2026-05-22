#!/usr/bin/env python3
"""Validate that GitHub Actions use immutable external action refs."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List


FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
USES_RE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^#\s]+)")
WORKFLOW_GLOBS = ("*.yml", "*.yaml")


def iter_workflow_files(workflow_dir: Path) -> Iterable[Path]:
    for pattern in WORKFLOW_GLOBS:
        yield from sorted(workflow_dir.glob(pattern))


def is_local_or_container_action(reference: str) -> bool:
    return reference.startswith(("./", "../", "docker://"))


def validate_action_pins(workflow_dir: Path) -> List[str]:
    errors: List[str] = []

    for path in iter_workflow_files(workflow_dir):
        lines = path.read_text().splitlines()
        for line_number, line in enumerate(lines, start=1):
            match = USES_RE.match(line)
            if not match:
                continue

            reference = match.group(1).strip().strip("'\"")
            if is_local_or_container_action(reference):
                continue

            if "@" not in reference:
                errors.append(
                    f"{path}:{line_number} external action is missing @ref"
                )
                continue

            action, ref = reference.rsplit("@", 1)
            if not FULL_SHA_RE.fullmatch(ref):
                errors.append(
                    f"{path}:{line_number} {action} uses mutable ref {ref!r}; "
                    "pin external actions to a full 40-character commit SHA"
                )

    return errors


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    workflow_dir = root / ".github" / "workflows"
    errors = validate_action_pins(workflow_dir)

    if errors:
        print("GitHub Actions pin validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("GitHub Actions pin validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
