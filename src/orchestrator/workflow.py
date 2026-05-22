"""Workflow Manager — Defines and executes multi-step agent workflows."""

import logging
import re
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)
TEMPLATE_PATTERN = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*}}")


class StepStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TemplateBindingError(ValueError):
    def __init__(self, step: "WorkflowStep", variables: List[str]):
        self.step = step
        self.variables = tuple(sorted(set(variables)))
        super().__init__(
            "Unresolved template variables: "
            + ", ".join(self.variables)
        )


class WorkflowStep:
    def __init__(
        self,
        name: str,
        handler: Callable,
        retries: int = 0,
        timeout: int = 300,
        parameters: Optional[Dict[str, Any]] = None,
    ):
        self.id = str(uuid4())
        self.name = name
        self.handler = handler
        self.retries = retries
        self.timeout = timeout
        self.parameters = parameters or {}
        self.bound_parameters: Dict[str, Any] = {}
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
    def __init__(self):
        self._workflows: Dict[str, Workflow] = {}
        self.audit_records: List[Dict[str, Any]] = []

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

    def execute_workflow(
        self,
        workflow_id: str,
        runtime_parameters: Optional[Dict[str, Any]] = None,
    ) -> bool:
        workflow = self._workflows.get(workflow_id)
        if not workflow:
            return False

        runtime_parameters = runtime_parameters or {}
        try:
            bound_parameters = self._prepare_workflow_parameters(
                workflow,
                runtime_parameters,
            )
        except TemplateBindingError as e:
            self._record_template_rejection(workflow, e)
            return False

        workflow.status = StepStatus.RUNNING
        for step in workflow.steps:
            step.status = StepStatus.RUNNING
            step.bound_parameters = bound_parameters[step.id]
            try:
                result = step.handler(**step.bound_parameters)
                step.result = result
                step.status = StepStatus.COMPLETED
            except Exception as e:
                step.error = str(e)
                step.status = StepStatus.FAILED
                workflow.status = StepStatus.FAILED
                return False

        workflow.status = StepStatus.COMPLETED
        return True

    def _prepare_workflow_parameters(
        self,
        workflow: Workflow,
        runtime_parameters: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        bound_parameters = {}
        for step in workflow.steps:
            unresolved: List[str] = []
            bound = self._bind_value(
                step.parameters,
                runtime_parameters,
                unresolved,
            )
            if unresolved:
                raise TemplateBindingError(step, unresolved)
            bound_parameters[step.id] = bound
        return bound_parameters

    def _bind_value(
        self,
        value: Any,
        runtime_parameters: Dict[str, Any],
        unresolved: List[str],
    ) -> Any:
        if isinstance(value, dict):
            return {
                key: self._bind_value(item, runtime_parameters, unresolved)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [
                self._bind_value(item, runtime_parameters, unresolved)
                for item in value
            ]
        if isinstance(value, tuple):
            return tuple(
                self._bind_value(item, runtime_parameters, unresolved)
                for item in value
            )
        if not isinstance(value, str):
            return value

        matches = list(TEMPLATE_PATTERN.finditer(value))
        if not matches:
            return value

        if len(matches) == 1 and matches[0].span() == (0, len(value)):
            variable = matches[0].group(1)
            if variable not in runtime_parameters:
                unresolved.append(variable)
                return value
            replacement = runtime_parameters[variable]
            if isinstance(replacement, str):
                for match in TEMPLATE_PATTERN.finditer(replacement):
                    unresolved.append(match.group(1))
            return replacement

        rendered = value
        for match in matches:
            variable = match.group(1)
            if variable not in runtime_parameters:
                unresolved.append(variable)
                continue
            rendered = rendered.replace(
                match.group(0),
                str(runtime_parameters[variable]),
            )

        for match in TEMPLATE_PATTERN.finditer(rendered):
            unresolved.append(match.group(1))
        return rendered

    def _record_template_rejection(
        self,
        workflow: Workflow,
        error: TemplateBindingError,
    ) -> None:
        record = {
            "event": "workflow_template_binding_rejected",
            "workflow_id": workflow.id,
            "step_id": error.step.id,
            "step_name": error.step.name,
            "unresolved_variables": list(error.variables),
        }
        self.audit_records.append(record)
        logger.warning(
            "Rejected workflow template binding for workflow %s step %s",
            workflow.id,
            error.step.id,
        )

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
