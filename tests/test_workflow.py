import pytest

from src.orchestrator.workflow import StepStatus, Workflow, WorkflowStep


def _handler():
    return "ok"


def test_workflow_rejects_implicit_sensitive_input_before_wiring():
    workflow = Workflow("claim processing")
    step = WorkflowStep(
        "notify handler",
        _handler,
        input_schema={
            "claim_id": {"type": "string"},
            "api_token": {"type": "string"},
        },
    )

    with pytest.raises(
        ValueError,
        match="sensitive inputs require explicit declaration",
    ):
        workflow.add_step(step)

    assert workflow.status is StepStatus.PENDING
    assert workflow.steps == []
    assert workflow.audit_events[-1]["decision"] == "rejected"
    assert (
        workflow.audit_events[-1]["reason"]
        == "sensitive inputs require explicit declaration"
    )
    assert workflow.audit_events[-1]["input_count"] == 2
    assert workflow.audit_events[-1]["sensitive_input_count"] == 0
    assert "api_token" not in str(workflow.audit_events[-1])


def test_workflow_accepts_explicit_sensitive_input_declaration():
    workflow = Workflow("claim processing")
    step = WorkflowStep(
        "notify handler",
        _handler,
        input_schema={
            "claim_id": {"type": "string"},
            "api_token": {"type": "string", "sensitive": True},
        },
        sensitive_inputs={"api_token"},
    )

    assert workflow.add_step(step) is workflow
    assert workflow.steps == [step]
    assert workflow.audit_events == []


def test_workflow_rejects_sensitive_rewire_during_lifecycle_transition():
    workflow = Workflow("claim processing")
    initial_step = WorkflowStep("prepare", _handler)
    workflow.add_step(initial_step)
    workflow.status = StepStatus.RUNNING

    stale_step = WorkflowStep(
        "late sensitive binding",
        _handler,
        input_schema={"password": {"type": "string", "sensitive": True}},
        sensitive_inputs={"password"},
    )

    with pytest.raises(ValueError, match="before execution starts"):
        workflow.add_step(stale_step)

    assert workflow.status is StepStatus.RUNNING
    assert workflow.steps == [initial_step]
    assert workflow.audit_events[-1]["workflow_status"] == "running"
    assert (
        workflow.audit_events[-1]["reason"]
        == "workflow steps can only be wired before execution starts"
    )
    assert "password" not in str(workflow.audit_events[-1])
