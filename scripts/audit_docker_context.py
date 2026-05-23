"""Audit Docker build-context hygiene without requiring Docker."""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, List, Sequence


DEFAULT_PROHIBITED_PROBES = (
    ".env",
    ".env.local",
    ".env.production",
    ".venv/bin/python",
    "venv/bin/python",
    "__pycache__/module.pyc",
    ".pytest_cache/CACHEDIR.TAG",
    ".mypy_cache/meta.json",
    ".ruff_cache/content",
    "logs/app.log",
    "debug/request.json",
    "scratch/note.txt",
    "tmp/session.json",
    "outputs/run.json",
    "artifacts/result.json",
    "dist/package.whl",
    "build/temp.o",
    "src/pkg.egg-info/PKG-INFO",
    ".coverage",
    "coverage.xml",
    "htmlcov/index.html",
    ".DS_Store",
    ".idea/workspace.xml",
    ".vscode/settings.json",
    "node_modules/pkg/index.js",
    "secrets/private.pem",
    "secrets/app.key",
    "local.sqlite",
)

PROHIBITED_SEGMENTS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "logs",
    "debug",
    "scratch",
    "tmp",
    "temp",
    "outputs",
    "artifacts",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    ".idea",
    ".vscode",
}
PROHIBITED_FILENAMES = {".coverage", "coverage.xml", ".DS_Store"}
PROHIBITED_SUFFIXES = {
    ".db",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pyc",
    ".pyo",
    ".sqlite",
    ".sqlite3",
}
BROAD_COPY_RE = re.compile(r"^\s*(COPY|ADD)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class IgnoreRule:
    pattern: str
    negated: bool = False
    directory_only: bool = False

    def matches(self, path: str) -> bool:
        normalized = path.strip("/")
        pattern = self.pattern.strip("/")
        if not normalized or not pattern:
            return False

        if self.directory_only:
            return _matches_directory(pattern, normalized)
        if "/" in pattern:
            return fnmatch.fnmatchcase(normalized, pattern)
        return any(
            fnmatch.fnmatchcase(part, pattern)
            for part in PurePosixPath(normalized).parts
        )


@dataclass(frozen=True)
class DockerContextAudit:
    missing_ignores: Sequence[str]
    unignored_prohibited: Sequence[str]
    broad_copy_patterns: Sequence[str]

    @property
    def ok(self) -> bool:
        return not (
            self.missing_ignores
            or self.unignored_prohibited
            or self.broad_copy_patterns
        )


def load_dockerignore(root: Path) -> List[IgnoreRule]:
    dockerignore = root / ".dockerignore"
    if not dockerignore.exists():
        return []

    rules: List[IgnoreRule] = []
    for raw_line in dockerignore.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        if negated:
            line = line[1:].strip()
        directory_only = line.endswith("/")
        rules.append(
            IgnoreRule(
                pattern=line.rstrip("/"),
                negated=negated,
                directory_only=directory_only,
            )
        )
    return rules


def is_ignored(path: str, rules: Iterable[IgnoreRule]) -> bool:
    ignored = False
    for rule in rules:
        if rule.matches(path):
            ignored = not rule.negated
    return ignored


def audit_docker_context(
    root: Path,
    probes: Sequence[str] = DEFAULT_PROHIBITED_PROBES,
) -> DockerContextAudit:
    rules = load_dockerignore(root)
    missing_ignores = sorted(
        probe for probe in probes if not is_ignored(probe, rules)
    )
    unignored_prohibited = scan_unignored_prohibited(root, rules)
    broad_copy_patterns = scan_broad_copy_patterns(root)
    return DockerContextAudit(
        missing_ignores=missing_ignores,
        unignored_prohibited=unignored_prohibited,
        broad_copy_patterns=broad_copy_patterns,
    )


def scan_unignored_prohibited(
    root: Path,
    rules: Sequence[IgnoreRule],
) -> List[str]:
    violations: List[str] = []
    for current, dirnames, filenames in os.walk(root):
        current_path = Path(current)
        relative_dir = _relative_path(current_path, root)
        kept_dirs = []
        for dirname in dirnames:
            relative = _join_relative(relative_dir, dirname)
            if is_ignored(relative, rules):
                continue
            if is_prohibited_path(relative):
                violations.append(relative)
                continue
            kept_dirs.append(dirname)
        dirnames[:] = kept_dirs

        for filename in filenames:
            relative = _join_relative(relative_dir, filename)
            if is_ignored(relative, rules):
                continue
            if is_prohibited_path(relative):
                violations.append(relative)
    return sorted(set(violations))


def scan_broad_copy_patterns(root: Path) -> List[str]:
    violations: List[str] = []
    for dockerfile in _dockerfiles(root):
        relative = _relative_path(dockerfile, root)
        for line_number, raw_line in enumerate(
            dockerfile.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _uses_broad_copy(stripped):
                violations.append(f"{relative}:{line_number}: {stripped}")
    return violations


def is_prohibited_path(path: str) -> bool:
    normalized = path.strip("/")
    parts = PurePosixPath(normalized).parts
    name = parts[-1] if parts else normalized
    if any(part in PROHIBITED_SEGMENTS for part in parts):
        return True
    if name in PROHIBITED_FILENAMES:
        return True
    if name.startswith(".env") and name not in {".env.example", ".env.sample"}:
        return True
    return any(name.endswith(suffix) for suffix in PROHIBITED_SUFFIXES)


def format_report(audit: DockerContextAudit) -> str:
    lines: List[str] = []
    if audit.missing_ignores:
        lines.append("Missing .dockerignore coverage:")
        lines.extend(f"  - {item}" for item in audit.missing_ignores)
    if audit.unignored_prohibited:
        lines.append("Unignored prohibited context entries:")
        lines.extend(f"  - {item}" for item in audit.unignored_prohibited)
    if audit.broad_copy_patterns:
        lines.append("Broad Dockerfile COPY/ADD patterns:")
        lines.extend(f"  - {item}" for item in audit.broad_copy_patterns)
    if not lines:
        return "Docker context policy passed."
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail when Docker context hygiene rules are violated."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root to audit.",
    )
    args = parser.parse_args()

    audit = audit_docker_context(args.root.resolve())
    print(format_report(audit))
    return 0 if audit.ok else 1


def _matches_directory(pattern: str, path: str) -> bool:
    if "/" in pattern:
        return path == pattern or path.startswith(f"{pattern}/")
    return any(
        part == pattern or fnmatch.fnmatchcase(part, pattern)
        for part in PurePosixPath(path).parts
    )


def _dockerfiles(root: Path) -> Iterable[Path]:
    for path in root.rglob("*"):
        if any(part in {".git", ".venv"} for part in path.parts):
            continue
        if path.is_file() and (
            path.name == "Dockerfile"
            or path.name.startswith("Dockerfile.")
            or path.name.endswith(".Dockerfile")
        ):
            yield path


def _uses_broad_copy(line: str) -> bool:
    if not BROAD_COPY_RE.match(line):
        return False
    normalized = (
        line.replace("[", " ")
        .replace("]", " ")
        .replace(",", " ")
        .replace('"', " ")
        .replace("'", " ")
    )
    tokens = normalized.split()
    sources = [token for token in tokens[1:-1] if not token.startswith("--")]
    return any(source in {".", "./"} for source in sources)


def _relative_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path.as_posix()
    return "" if relative == Path(".") else relative.as_posix()


def _join_relative(relative_dir: str, name: str) -> str:
    return name if not relative_dir else f"{relative_dir}/{name}"


if __name__ == "__main__":
    raise SystemExit(main())
