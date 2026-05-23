from pathlib import Path

from scripts.audit_docker_context import (
    DEFAULT_PROHIBITED_PROBES,
    audit_docker_context,
    format_report,
    is_ignored,
    load_dockerignore,
)


def test_root_dockerignore_excludes_local_debug_and_generated_files():
    root = Path(__file__).resolve().parents[1]
    rules = load_dockerignore(root)

    missing = [
        probe
        for probe in DEFAULT_PROHIBITED_PROBES
        if not is_ignored(probe, rules)
    ]

    assert missing == []


def test_audit_fails_when_required_probe_is_not_ignored(tmp_path):
    (tmp_path / ".dockerignore").write_text("*.log\n", encoding="utf-8")

    audit = audit_docker_context(tmp_path, probes=[".env", "debug.log"])

    assert ".env" in audit.missing_ignores
    assert not audit.ok
    assert "Missing .dockerignore coverage" in format_report(audit)


def test_audit_detects_unignored_debug_files_in_context(tmp_path):
    (tmp_path / ".dockerignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / "debug.log").write_text("local trace\n", encoding="utf-8")

    audit = audit_docker_context(tmp_path, probes=[".env"])

    assert audit.unignored_prohibited == ["debug.log"]
    assert not audit.ok


def test_audit_detects_broad_dockerfile_copy_patterns(tmp_path):
    (tmp_path / ".dockerignore").write_text(
        ".env\nlogs/\n.venv/\n",
        encoding="utf-8",
    )
    (tmp_path / "Dockerfile").write_text(
        "FROM python:3.11-slim\nCOPY . /app\nADD [\"./\", \"/app\"]\n",
        encoding="utf-8",
    )

    audit = audit_docker_context(
        tmp_path,
        probes=[".env", "logs/app.log", ".venv/bin/python"],
    )

    assert audit.broad_copy_patterns == [
        "Dockerfile:2: COPY . /app",
        "Dockerfile:3: ADD [\"./\", \"/app\"]",
    ]
    assert not audit.ok


def test_audit_passes_for_narrow_copy_policy(tmp_path):
    (tmp_path / ".dockerignore").write_text(
        ".env\nlogs/\n.venv/\ndebug/\n",
        encoding="utf-8",
    )
    (tmp_path / "Dockerfile").write_text(
        "\n".join(
            [
                "FROM python:3.11-slim",
                "COPY pyproject.toml README.md /app/",
                "COPY src/ /app/src/",
            ]
        ),
        encoding="utf-8",
    )

    audit = audit_docker_context(
        tmp_path,
        probes=[
            ".env",
            "logs/app.log",
            ".venv/bin/python",
            "debug/request.json",
        ],
    )

    assert audit.ok
