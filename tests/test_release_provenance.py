from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release.yml"
DOC_PATH = ROOT / "docs" / "release-provenance.md"


def _workflow():
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _paths(value):
    return {line.strip() for line in value.splitlines() if line.strip()}


def _step_with_action(
    steps,
    action,
):
    return next(
        step for step in steps
        if step.get("uses", "").startswith(action)
    )


def _step_named(steps, name):
    return next(step for step in steps if step.get("name") == name)


def test_release_workflow_runs_on_version_tags():
    workflow = _workflow()

    assert workflow["on"]["push"]["tags"] == ["v*"]


def test_release_workflow_grants_attestation_permissions():
    permissions = _workflow()["permissions"]

    assert permissions["contents"] == "write"
    assert permissions["id-token"] == "write"
    assert permissions["attestations"] == "write"


def test_attested_paths_match_published_release_assets():
    steps = _workflow()["jobs"]["package"]["steps"]
    attest = _step_with_action(steps, "actions/attest-build-provenance@")
    upload = _step_with_action(steps, "actions/upload-artifact@")
    release = _step_with_action(steps, "softprops/action-gh-release@")
    expected_paths = {
        "dist/*.whl",
        "dist/*.tar.gz",
        "dist/SHA256SUMS",
    }

    assert _paths(attest["with"]["subject-path"]) == expected_paths
    assert _paths(upload["with"]["path"]) == expected_paths
    assert _paths(release["with"]["files"]) == expected_paths
    assert upload["with"]["if-no-files-found"] == "error"
    assert release["with"]["fail_on_unmatched_files"] is True


def test_release_workflow_validates_tag_and_source_archive():
    steps = _workflow()["jobs"]["package"]["steps"]
    tag_validation = _step_named(
        steps,
        "Validate release tag matches package version",
    )
    source_archive = _step_named(steps, "Create repository source archive")

    assert "GITHUB_REF_NAME" in tag_validation["run"]
    assert "pyproject.toml" in tag_validation["run"]
    assert "git archive" in source_archive["run"]
    assert (
        "AgentOrchestration-${GITHUB_REF_NAME}.tar.gz"
        in source_archive["run"]
    )


def test_release_docs_include_attestation_verification_steps():
    text = DOC_PATH.read_text(encoding="utf-8").lower()

    assert "gh attestation verify" in text
    assert "--repo orchestration-agent/agentorchestration" in text
    assert "--source-ref refs/tags/" in text
    assert "source repository" in text
    assert "commit" in text
    assert "workflow" in text
    assert "digest" in text
