#!/usr/bin/env python3
"""Audit Docker build context size before image builds."""

from __future__ import annotations

import argparse
import fnmatch
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

DEFAULT_BUDGET_BYTES = 15 * 1024 * 1024
DEFAULT_IGNORE_RULES = (".git",)
PROHIBITED_CONTEXT_PATTERNS = (
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "build",
    "dist",
    "*.egg-info",
    "node_modules",
    "coverage",
    ".coverage",
    ".DS_Store",
    "outputs",
    "artifacts",
    "tmp",
)


@dataclass(frozen=True)
class ContextEntry:
    path: str
    size: int


@dataclass(frozen=True)
class ContextAudit:
    total_bytes: int
    budget_bytes: int
    top_entries: Tuple[ContextEntry, ...]
    prohibited_entries: Tuple[ContextEntry, ...]

    @property
    def ok(self) -> bool:
        within_budget = self.total_bytes <= self.budget_bytes
        return within_budget and not self.prohibited_entries


def _normalize_pattern(pattern: str) -> str:
    pattern = pattern.strip().replace("\\", "/")
    while pattern.startswith("./"):
        pattern = pattern[2:]
    return pattern.lstrip("/")


def load_ignore_rules(root: Path) -> List[str]:
    rules = list(DEFAULT_IGNORE_RULES)
    dockerignore = root / ".dockerignore"
    if dockerignore.exists():
        for raw_line in dockerignore.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#"):
                rules.append(_normalize_pattern(line))
    return rules


def _rule_matches(rule: str, rel_path: str, is_dir: bool) -> bool:
    rule = _normalize_pattern(rule.lstrip("!"))
    directory_rule = rule.endswith("/")
    if directory_rule:
        rule = rule.rstrip("/")

    if not rule:
        return False

    parts = rel_path.split("/")
    if "/" not in rule:
        if any(fnmatch.fnmatch(part, rule) for part in parts):
            return True
        return directory_rule and is_dir and fnmatch.fnmatch(parts[-1], rule)

    if fnmatch.fnmatch(rel_path, rule):
        return True
    return rel_path.startswith(f"{rule}/")


def is_ignored(rel_path: str, is_dir: bool, rules: Sequence[str]) -> bool:
    ignored = False
    for raw_rule in rules:
        if _rule_matches(raw_rule, rel_path, is_dir):
            ignored = not raw_rule.startswith("!")
    return ignored


def _iter_context_files(
    root: Path,
    rules: Sequence[str],
) -> Iterable[ContextEntry]:
    for current_root, dir_names, file_names in os.walk(root):
        current_path = Path(current_root)
        kept_dirs = []
        for dir_name in dir_names:
            rel_dir = (current_path / dir_name).relative_to(root).as_posix()
            if not is_ignored(rel_dir, True, rules):
                kept_dirs.append(dir_name)
        dir_names[:] = kept_dirs

        for file_name in file_names:
            file_path = current_path / file_name
            rel_file = file_path.relative_to(root).as_posix()
            if is_ignored(rel_file, False, rules):
                continue
            try:
                size = file_path.lstat().st_size
            except OSError:
                continue
            yield ContextEntry(path=rel_file, size=size)


def _matches_prohibited(entry: ContextEntry) -> bool:
    parts = entry.path.split("/")
    for pattern in PROHIBITED_CONTEXT_PATTERNS:
        if "/" in pattern:
            matches_path = fnmatch.fnmatch(entry.path, pattern)
            matches_child = entry.path.startswith(f"{pattern}/")
            if matches_path or matches_child:
                return True
        elif any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


def audit_context(
    root: Path,
    budget_bytes: int = DEFAULT_BUDGET_BYTES,
    top_limit: int = 10,
) -> ContextAudit:
    rules = load_ignore_rules(root)
    entries = tuple(_iter_context_files(root, rules))
    sorted_entries = sorted(
        entries,
        key=lambda entry: entry.size,
        reverse=True,
    )
    top_entries = tuple(sorted_entries[:top_limit])
    prohibited_entries = tuple(
        entry for entry in entries if _matches_prohibited(entry)
    )
    return ContextAudit(
        total_bytes=sum(entry.size for entry in entries),
        budget_bytes=budget_bytes,
        top_entries=top_entries,
        prohibited_entries=prohibited_entries,
    )


def format_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}" if unit != "B" else f"{value} B"
        amount /= 1024
    return f"{value} B"


def render_report(audit: ContextAudit) -> str:
    lines = [
        f"Docker build context size: {format_bytes(audit.total_bytes)}",
        f"Docker build context budget: {format_bytes(audit.budget_bytes)}",
    ]

    if audit.top_entries:
        lines.append("Largest context entries:")
        for entry in audit.top_entries:
            lines.append(f"- {entry.path}: {format_bytes(entry.size)}")

    if audit.total_bytes > audit.budget_bytes:
        excess_bytes = audit.total_bytes - audit.budget_bytes
        lines.append(
            f"Context exceeds budget by {format_bytes(excess_bytes)}."
        )

    if audit.prohibited_entries:
        lines.append(
            "Prohibited generated/cache entries included in Docker context:"
        )
        for entry in audit.prohibited_entries:
            lines.append(f"- {entry.path}: {format_bytes(entry.size)}")

    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root used as the Docker build context.",
    )
    parser.add_argument(
        "--budget-bytes",
        type=int,
        default=int(
            os.environ.get("DOCKER_CONTEXT_BUDGET_BYTES", DEFAULT_BUDGET_BYTES)
        ),
        help="Maximum allowed Docker context size in bytes.",
    )
    parser.add_argument(
        "--top-limit",
        type=int,
        default=10,
        help="Number of largest entries to report.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    audit = audit_context(
        Path(args.root).resolve(),
        args.budget_bytes,
        args.top_limit,
    )
    print(render_report(audit))
    return 0 if audit.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
