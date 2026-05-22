import importlib.util
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "validate_github_action_pins.py"
)
SPEC = importlib.util.spec_from_file_location(
    "action_pin_validator",
    SCRIPT_PATH,
)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


def _write_workflow(tmp_path, content):
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    (workflow_dir / "ci.yml").write_text(content)
    return workflow_dir


def test_validate_action_pins_accepts_full_sha_and_local_actions(tmp_path):
    workflow_dir = _write_workflow(
        tmp_path,
        """
jobs:
  test:
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5
      - uses: ./.github/actions/local-check
      - uses: docker://alpine:3.20
""",
    )

    assert validator.validate_action_pins(workflow_dir) == []


def test_validate_action_pins_rejects_mutable_tag(tmp_path):
    workflow_dir = _write_workflow(
        tmp_path,
        """
jobs:
  test:
    steps:
      - uses: actions/checkout@v4
""",
    )

    errors = validator.validate_action_pins(workflow_dir)

    assert len(errors) == 1
    assert "actions/checkout" in errors[0]
    assert "mutable ref 'v4'" in errors[0]


def test_validate_action_pins_rejects_missing_ref(tmp_path):
    workflow_dir = _write_workflow(
        tmp_path,
        """
jobs:
  test:
    steps:
      - uses: actions/checkout
""",
    )

    errors = validator.validate_action_pins(workflow_dir)

    assert len(errors) == 1
    assert "missing @ref" in errors[0]
