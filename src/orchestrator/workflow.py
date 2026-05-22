"""Workflow Manager — Defines and executes multi-step agent workflows."""

from enum import Enum
import inspect
import time
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class WorkflowStep:
    def __init__(
        self,
        name: str,
        handler: Callable,
        retries: int = 0,
        timeout: int = 300,
        condition: Optional[Callable] = None,
    ):
        self.id = str(uuid4())
        self.name = name
        self.handler = handler
        self.condition = condition
        self.retries = retries
        self.timeout = timeout
        self.status = StepStatus.PENDING
        self.result: Any = None
        self.error: Optional[str] = None


class Workflow:
    def __init__(self, name: str, description: str = ""):
        self.id = str(uuid4())
        self.name = name
        self.description = description
        self.steps: List[WorkflowStep] = []
        self._step_map: Dict[str, WorkflowStep] = {}
        self.status = StepStatus.PENDING

    def add_step(self, step: WorkflowStep) -> "Workflow":
        self.steps.append(step)
        self._step_map[step.id] = step
        return self

    def get_step(self, step_id: str) -> Optional[WorkflowStep]:
        return self._step_map.get(step_id)


class WorkflowManager:
    def __init__(self, audit_limit: int = 100):
        self._workflows: Dict[str, Workflow] = {}
        self._audit_limit = audit_limit
        self._audit_log: List[Dict[str, Any]] = []

    def create_workflow(self, name: str, description: str = "") -> Workflow:
        workflow = Workflow(name, description)
        self._workflows[workflow.id] = workflow
        return workflow

    def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        return self._workflows.get(workflow_id)

    def list_workflows(self) -> List[Workflow]:
        return list(self._workflows.values())

    def delete_workflow(self, workflow_id: str) -> bool:
        return self._workflows.pop(workflow_id, None) is not None

    def execute_workflow(self, workflow_id: str) -> bool:
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return False

        workflow.status = StepStatus.RUNNING
        for step in workflow.steps:
            if not self._pre_dispatch_condition_allows(workflow, step):
                if step.status == StepStatus.SKIPPED:
                    continue
                workflow.status = StepStatus.FAILED
                return False

            try:
                step.status = StepStatus.RUNNING
                self._record_audit(
                    workflow,
                    step,
                    "transition",
                    "step_running",
                )
                result = step.handler()
                step.result = result
                step.status = StepStatus.COMPLETED
                self._record_audit(
                    workflow,
                    step,
                    "transition",
                    "step_completed",
                )
            except Exception as e:
                step.error = str(e)
                step.status = StepStatus.FAILED
                workflow.status = StepStatus.FAILED
                self._record_audit(
                    workflow,
                    step,
                    "transition",
                    "handler_failed",
                )
                return False

        workflow.status = StepStatus.COMPLETED
        return True

    def audit_log(self) -> List[Dict[str, Any]]:
        return [dict(entry) for entry in self._audit_log]

    def _pre_dispatch_condition_allows(
        self,
        workflow: Workflow,
        step: WorkflowStep,
    ) -> bool:
        if step.condition is None:
            return True

        snapshot = self._condition_snapshot(workflow)
        try:
            condition_result = self._call_condition(
                step.condition,
                workflow,
                step,
            )
        except Exception as e:
            self._restore_condition_snapshot(workflow, snapshot)
            step.error = str(e)
            step.status = StepStatus.FAILED
            self._record_audit(workflow, step, "reject", "condition_failed")
            return False

        if not isinstance(condition_result, bool):
            self._restore_condition_snapshot(workflow, snapshot)
            step.error = "condition returned non-boolean result"
            step.status = StepStatus.FAILED
            self._record_audit(
                workflow,
                step,
                "reject",
                "condition_non_boolean",
            )
            return False

        if self._condition_fingerprint(workflow) != snapshot["fingerprint"]:
            self._restore_condition_snapshot(workflow, snapshot)
            step.error = "condition attempted lifecycle side effects"
            step.status = StepStatus.FAILED
            self._record_audit(
                workflow,
                step,
                "reject",
                "condition_side_effect",
            )
            return False

        if not condition_result:
            step.status = StepStatus.SKIPPED
            self._record_audit(workflow, step, "transition", "condition_false")
            return False

        self._record_audit(workflow, step, "allow", "condition_true")
        return True

    def _call_condition(
        self,
        condition: Callable,
        workflow: Workflow,
        step: WorkflowStep,
    ) -> Any:
        try:
            parameter_count = len(inspect.signature(condition).parameters)
        except (TypeError, ValueError):
            parameter_count = 0

        if parameter_count >= 2:
            return condition(workflow, step)
        if parameter_count == 1:
            return condition(workflow)
        return condition()

    def _condition_snapshot(
        self,
        workflow: Workflow,
    ) -> Dict[str, Any]:
        step_values = [
            (
                step,
                step.name,
                step.handler,
                step.condition,
                step.retries,
                step.timeout,
                step.status,
                step.result,
                step.error,
            )
            for step in workflow.steps
        ]
        return {
            "workflow_status": workflow.status,
            "steps": list(workflow.steps),
            "step_map": dict(workflow._step_map),
            "step_values": step_values,
            "fingerprint": self._condition_fingerprint(workflow),
        }

    def _restore_condition_snapshot(
        self,
        workflow: Workflow,
        snapshot: Dict[str, Any],
    ) -> None:
        workflow.status = snapshot["workflow_status"]
        workflow.steps = list(snapshot["steps"])
        workflow._step_map = dict(snapshot["step_map"])
        for (
            step,
            name,
            handler,
            condition,
            retries,
            timeout,
            status,
            result,
            error,
        ) in snapshot["step_values"]:
            step.name = name
            step.handler = handler
            step.condition = condition
            step.retries = retries
            step.timeout = timeout
            step.status = status
            step.result = result
            step.error = error

    def _condition_fingerprint(
        self,
        workflow: Workflow,
    ) -> Tuple[Any, ...]:
        return (
            workflow.status,
            tuple(step.id for step in workflow.steps),
            tuple(
                sorted(
                    (step_id, id(step))
                    for step_id, step in workflow._step_map.items()
                )
            ),
            tuple(
                (
                    id(step),
                    step.id,
                    step.name,
                    id(step.handler),
                    id(step.condition),
                    step.retries,
                    step.timeout,
                    step.status,
                    step.result,
                    step.error,
                )
                for step in workflow.steps
            ),
        )

    def _record_audit(
        self,
        workflow: Workflow,
        step: WorkflowStep,
        action: str,
        reason: str,
    ) -> None:
        self._audit_log.append({
            "timestamp": time.time(),
            "action": action,
            "reason": reason,
            "workflow_id": workflow.id,
            "workflow_status": workflow.status.value,
            "step_id": step.id,
            "step_status": step.status.value,
        })
        if len(self._audit_log) > self._audit_limit:
            self._audit_log = self._audit_log[-self._audit_limit:]

# 2019-03-27T19:58:07 update

# 2019-05-09T09:42:56 update

# 2019-12-03T10:07:42 update

# 2020-01-16T18:43:28 update

# 2020-03-20T10:40:15 update

# 2020-04-17T15:36:50 update

# 2020-05-04T14:44:01 update

# 2020-06-16T13:17:31 update

# 2020-08-05T17:00:24 update

# 2020-09-04T08:29:23 update

# 2020-09-09T17:52:02 update

# 2020-10-23T10:57:44 update

# 2020-12-05T20:55:47 update

# 2021-01-15T19:23:40 update

# 2021-02-03T20:43:12 update

# 2021-03-16T12:26:47 update

# 2021-04-20T14:33:28 update

# 2021-10-14T15:03:32 update

# 2021-10-21T17:24:55 update

# 2021-11-16T17:01:08 update

# 2021-11-22T09:51:21 update

# 2021-12-21T16:15:47 update

# 2022-03-23T16:52:27 update

# 2022-12-21T09:25:50 update

# 2023-01-09T09:55:25 update

# 2023-01-13T11:06:15 update

# 2023-01-26T11:00:59 update

# 2023-02-23T08:56:54 update

# 2023-05-17T08:07:16 update

# 2023-06-06T17:09:34 update

# 2023-06-13T10:35:28 update

# 2023-08-24T20:36:06 update

# 2023-10-30T19:10:13 update

# 2024-01-02T08:27:25 update

# 2024-01-24T12:13:15 update

# 2024-02-08T13:35:49 update

# 2024-05-07T16:09:24 update

# 2024-05-11T09:48:46 update

# 2024-05-21T19:25:41 update

# 2024-06-05T12:00:30 update

# 2024-06-25T09:40:26 update

# 2024-09-17T13:49:39 update

# 2024-10-14T17:39:35 update

# 2024-11-27T20:14:35 update

# 2024-12-25T19:31:41 update

# 2025-01-16T13:15:09 update

# 2025-02-05T14:06:59 update

# 2025-02-17T20:55:11 update

# 2025-04-30T19:36:53 update

# 2025-07-17T10:14:40 update

# 2025-08-29T12:13:15 update

# 2025-09-03T13:51:11 update

# 2025-09-19T16:08:24 update

# 2025-11-27T08:38:12 update

# 2026-01-27T13:23:38 update

# 2026-01-28T11:22:50 update
