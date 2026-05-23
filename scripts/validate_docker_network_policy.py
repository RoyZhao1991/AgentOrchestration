"""Validate that Docker runtime packaging cannot use network fetches."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable, List, Tuple


DOCKERFILE = Path("Dockerfile")
FINAL_STAGE = "runtime"
FETCH_PATTERNS = (
    re.compile(r"\bapt(-get)?\s+(update|install)\b"),
    re.compile(r"\b(apk|dnf|yum)\s+(add|install|update)\b"),
    re.compile(r"\b(curl|wget)\b"),
    re.compile(r"\buv\s+(sync|pip|add)\b"),
    re.compile(r"\bpip\s+install\b(?!.*--no-index)"),
    re.compile(r"\bpython\s+-m\s+pip\s+install\b(?!.*--no-index)"),
)


def _logical_lines(text: str) -> List[str]:
    lines: List[str] = []
    current = ""
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].rstrip()
        if not line and not current:
            continue
        if line.endswith("\\"):
            current += line[:-1].strip() + " "
            continue
        current += line.strip()
        if current:
            lines.append(current)
        current = ""
    if current:
        lines.append(current)
    return lines


def _stages(lines: Iterable[str]) -> List[Tuple[str, List[str]]]:
    stages: List[Tuple[str, List[str]]] = []
    current_name = ""
    current_lines: List[str] = []

    for line in lines:
        if line.upper().startswith("FROM "):
            if current_name:
                stages.append((current_name, current_lines))
            match = re.search(r"\s+AS\s+([A-Za-z0-9_-]+)\s*$", line, re.I)
            current_name = match.group(1).lower() if match else ""
            current_lines = [line]
        elif current_name:
            current_lines.append(line)

    if current_name:
        stages.append((current_name, current_lines))
    return stages


def validate_dockerfile_text(text: str) -> List[str]:
    lines = _logical_lines(text)
    stages = _stages(lines)
    stage_map = {name: stage_lines for name, stage_lines in stages}
    errors: List[str] = []

    final_lines = stage_map.get(FINAL_STAGE)
    if not final_lines:
        return [f"missing final stage named {FINAL_STAGE!r}"]

    if not any("--network=none" in line for line in final_lines):
        errors.append("runtime stage must include a RUN --network=none check")

    for line in final_lines:
        for pattern in FETCH_PATTERNS:
            if pattern.search(line):
                errors.append(f"network fetch in runtime stage: {line}")
                break

    if not any(
        line.startswith("COPY --from=dependencies ") for line in final_lines
    ):
        errors.append(
            "runtime stage must copy local artifacts from dependencies"
        )

    return errors


def main() -> int:
    text = DOCKERFILE.read_text(encoding="utf-8")
    errors = validate_dockerfile_text(text)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("Docker final stage network policy passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
