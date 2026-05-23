import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "check_docker_context.py"
)
MODULE_NAME = "check_docker_context"
SPEC = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT_PATH)
check_docker_context = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = check_docker_context
SPEC.loader.exec_module(check_docker_context)


def test_audit_context_reports_size_and_top_entries(tmp_path):
    (tmp_path / "small.txt").write_text("abc", encoding="utf-8")
    (tmp_path / "large.bin").write_bytes(b"x" * 32)

    audit = check_docker_context.audit_context(
        tmp_path,
        budget_bytes=128,
        top_limit=1,
    )

    assert audit.ok
    assert audit.total_bytes == 35
    assert audit.top_entries[0].path == "large.bin"


def test_audit_context_fails_when_budget_is_exceeded(tmp_path):
    (tmp_path / "large.bin").write_bytes(b"x" * 32)

    audit = check_docker_context.audit_context(
        tmp_path,
        budget_bytes=8,
        top_limit=1,
    )
    report = check_docker_context.render_report(audit)

    assert not audit.ok
    assert "Context exceeds budget" in report
    assert "large.bin" in report


def test_audit_context_lists_prohibited_generated_entries(tmp_path):
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "artifact.bin").write_bytes(b"x" * 12)

    audit = check_docker_context.audit_context(
        tmp_path,
        budget_bytes=128,
        top_limit=5,
    )
    report = check_docker_context.render_report(audit)

    assert not audit.ok
    assert "Prohibited generated/cache entries" in report
    assert "outputs/artifact.bin" in report


def test_dockerignore_excludes_generated_entries(tmp_path):
    (tmp_path / ".dockerignore").write_text("outputs/\n", encoding="utf-8")
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    (output_dir / "artifact.bin").write_bytes(b"x" * 12)
    (tmp_path / "app.py").write_text("print('ok')\n", encoding="utf-8")

    audit = check_docker_context.audit_context(
        tmp_path,
        budget_bytes=128,
        top_limit=5,
    )

    assert audit.ok
    assert not audit.prohibited_entries
    assert {entry.path for entry in audit.top_entries} == {
        ".dockerignore",
        "app.py",
    }
