from src.orchestrator.workflow import (
    StepStatus,
    WorkflowManager,
    WorkflowStep,
)


def test_execute_workflow_rejects_unresolved_template_variables():
    manager = WorkflowManager()
    workflow = manager.create_workflow("deploy")
    calls = []

    def handler(target):
        calls.append(target)
        return {"target": target}

    step = WorkflowStep(
        "bind target",
        handler,
        parameters={"target": "{{ missing_target }}"},
    )
    workflow.add_step(step)

    executed = manager.execute_workflow(
        workflow.id,
        runtime_parameters={"safe_target": "worker-1"},
    )

    assert executed is False
    assert calls == []
    assert workflow.status is StepStatus.PENDING
    assert step.status is StepStatus.PENDING
    assert step.bound_parameters == {}
    assert manager.audit_records == [
        {
            "event": "workflow_template_binding_rejected",
            "workflow_id": workflow.id,
            "step_id": step.id,
            "step_name": "bind target",
            "unresolved_variables": ["missing_target"],
        }
    ]


def test_execute_workflow_binds_runtime_parameters_before_dispatch():
    manager = WorkflowManager()
    workflow = manager.create_workflow("deploy")

    def handler(target, message):
        return f"{target}:{message}"

    step = WorkflowStep(
        "bind target",
        handler,
        parameters={
            "target": "{{ target }}",
            "message": "deploy {{ release }}",
        },
    )
    workflow.add_step(step)

    executed = manager.execute_workflow(
        workflow.id,
        runtime_parameters={
            "target": "worker-1",
            "release": "2026.05",
        },
    )

    assert executed is True
    assert workflow.status is StepStatus.COMPLETED
    assert step.status is StepStatus.COMPLETED
    assert step.bound_parameters == {
        "target": "worker-1",
        "message": "deploy 2026.05",
    }
    assert step.result == "worker-1:deploy 2026.05"
    assert manager.audit_records == []


def test_execute_workflow_rejects_nested_unresolved_templates_without_values():
    manager = WorkflowManager()
    workflow = manager.create_workflow("fanout")
    sensitive_value = "sensitive-runtime-material"

    def handler(payload):
        return payload

    step = WorkflowStep(
        "bind payload",
        handler,
        parameters={
            "payload": {
                "secret": sensitive_value,
                "destination": "queue://{{ destination }}",
            }
        },
    )
    workflow.add_step(step)

    executed = manager.execute_workflow(workflow.id)

    assert executed is False
    assert workflow.status is StepStatus.PENDING
    assert step.status is StepStatus.PENDING
    assert sensitive_value not in str(manager.audit_records)
    assert manager.audit_records[0]["unresolved_variables"] == [
        "destination",
    ]


def test_execute_workflow_rejects_runtime_values_that_still_have_templates():
    manager = WorkflowManager()
    workflow = manager.create_workflow("release")

    def handler(target):
        return target

    step = WorkflowStep(
        "bind target",
        handler,
        parameters={"target": "{{ target }}"},
    )
    workflow.add_step(step)

    executed = manager.execute_workflow(
        workflow.id,
        runtime_parameters={"target": "{{ stale_target }}"},
    )

    assert executed is False
    assert workflow.status is StepStatus.PENDING
    assert step.status is StepStatus.PENDING
    assert manager.audit_records[0]["unresolved_variables"] == [
        "stale_target",
    ]
