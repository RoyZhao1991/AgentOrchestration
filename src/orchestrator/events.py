"""Event dispatch guards for orchestrator lifecycle transitions."""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Set, Tuple

from src.common.metrics import MetricsCollector, metrics

logger = logging.getLogger(__name__)


class LifecycleState(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class DispatchAction(Enum):
    COMMITTED = "committed"
    DEFERRED = "deferred"
    QUARANTINED = "quarantined"


DEFAULT_KNOWN_EVENT_TYPES = frozenset(
    {
        "agent.started",
        "agent.paused",
        "agent.completed",
        "agent.failed",
        "handler.started",
        "handler.completed",
        "handler.failed",
        "task.started",
        "task.paused",
        "task.completed",
        "task.failed",
        "workflow.started",
        "workflow.completed",
        "workflow.failed",
    }
)


ALLOWED_TRANSITIONS = {
    LifecycleState.PENDING: {
        LifecycleState.RUNNING,
        LifecycleState.FAILED,
    },
    LifecycleState.RUNNING: {
        LifecycleState.PAUSED,
        LifecycleState.COMPLETED,
        LifecycleState.FAILED,
    },
    LifecycleState.PAUSED: {
        LifecycleState.RUNNING,
        LifecycleState.COMPLETED,
        LifecycleState.FAILED,
    },
    LifecycleState.COMPLETED: set(),
    LifecycleState.FAILED: set(),
}


@dataclass(frozen=True)
class OrchestratorEvent:
    run_id: str
    event_type: str
    lifecycle_state: LifecycleState
    attempt: int
    revision: int
    version: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunLifecycle:
    lifecycle_state: LifecycleState = LifecycleState.PENDING
    attempt: int = 0
    revision: int = 0
    version: str = "1.0.0"
    upgrading_to: Optional[str] = None

    @property
    def is_upgrading(self) -> bool:
        return self.upgrading_to is not None


@dataclass(frozen=True)
class DispatchDecision:
    action: DispatchAction
    reason: str
    run_id: str
    event_type: str
    lifecycle_state: LifecycleState
    committed_lifecycle_state: LifecycleState
    committed_attempt: int
    committed_revision: int
    audit_record: Dict[str, Any]

    @property
    def committed(self) -> bool:
        return self.action == DispatchAction.COMMITTED

    @property
    def deferred(self) -> bool:
        return self.action == DispatchAction.DEFERRED

    @property
    def quarantined(self) -> bool:
        return self.action == DispatchAction.QUARANTINED


class OrchestratorEventDispatcher:
    """Guards event transitions before committing lifecycle state."""

    def __init__(
        self,
        known_event_types: Optional[Sequence[str]] = None,
        metrics_collector: Optional[MetricsCollector] = None,
    ):
        self.known_event_types: Set[str] = set(
            known_event_types or DEFAULT_KNOWN_EVENT_TYPES
        )
        self._metrics = metrics_collector or metrics
        self._states: Dict[str, RunLifecycle] = {}
        self._quarantine: Dict[str, Tuple[OrchestratorEvent, ...]] = {}
        self._deferred: Dict[str, Tuple[OrchestratorEvent, ...]] = {}
        self._audit_records = []

    def get_state(self, run_id: str) -> RunLifecycle:
        return self._states.get(run_id, RunLifecycle())

    def audit_records(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(self._audit_records)

    def quarantined_events(self, run_id: str) -> Tuple[OrchestratorEvent, ...]:
        return self._quarantine.get(run_id, ())

    def deferred_events(self, run_id: str) -> Tuple[OrchestratorEvent, ...]:
        return self._deferred.get(run_id, ())

    def start_rolling_upgrade(
        self,
        run_id: str,
        target_version: str,
    ) -> RunLifecycle:
        state = self.get_state(run_id)
        upgraded = RunLifecycle(
            lifecycle_state=state.lifecycle_state,
            attempt=state.attempt,
            revision=state.revision,
            version=state.version,
            upgrading_to=target_version,
        )
        self._states[run_id] = upgraded
        return upgraded

    def finish_rolling_upgrade(self, run_id: str) -> RunLifecycle:
        state = self.get_state(run_id)
        if not state.upgrading_to:
            return state
        upgraded = RunLifecycle(
            lifecycle_state=state.lifecycle_state,
            attempt=state.attempt,
            revision=state.revision,
            version=state.upgrading_to,
            upgrading_to=None,
        )
        self._states[run_id] = upgraded
        return upgraded

    def dispatch(self, event: OrchestratorEvent) -> DispatchDecision:
        state = self.get_state(event.run_id)
        if event.event_type not in self.known_event_types:
            return self._quarantine_event(
                event,
                state,
                "unknown_event_type",
            )
        if event.attempt < state.attempt:
            return self._quarantine_event(
                event,
                state,
                "stale_attempt",
            )
        if event.revision <= state.revision:
            return self._quarantine_event(
                event,
                state,
                "stale_or_duplicate_revision",
            )
        if state.is_upgrading and event.version != state.upgrading_to:
            return self._defer_event(
                event,
                state,
                "version_mismatch_during_rolling_upgrade",
            )
        if not self._transition_allowed(
            state.lifecycle_state,
            event.lifecycle_state,
        ):
            return self._quarantine_event(
                event,
                state,
                "invalid_lifecycle_transition",
            )
        committed = RunLifecycle(
            lifecycle_state=event.lifecycle_state,
            attempt=event.attempt,
            revision=event.revision,
            version=event.version,
            upgrading_to=state.upgrading_to,
        )
        self._states[event.run_id] = committed
        return self._decision(
            DispatchAction.COMMITTED,
            "transition_committed",
            event,
            committed,
        )

    def _transition_allowed(
        self,
        current: LifecycleState,
        requested: LifecycleState,
    ) -> bool:
        return requested in ALLOWED_TRANSITIONS[current]

    def _quarantine_event(
        self,
        event: OrchestratorEvent,
        state: RunLifecycle,
        reason: str,
    ) -> DispatchDecision:
        self._append_event(self._quarantine, event)
        return self._decision(
            DispatchAction.QUARANTINED,
            reason,
            event,
            state,
        )

    def _defer_event(
        self,
        event: OrchestratorEvent,
        state: RunLifecycle,
        reason: str,
    ) -> DispatchDecision:
        self._append_event(self._deferred, event)
        return self._decision(
            DispatchAction.DEFERRED,
            reason,
            event,
            state,
        )

    def _append_event(
        self,
        storage: Dict[str, Tuple[OrchestratorEvent, ...]],
        event: OrchestratorEvent,
    ) -> None:
        storage[event.run_id] = storage.get(event.run_id, ()) + (event,)

    def _decision(
        self,
        action: DispatchAction,
        reason: str,
        event: OrchestratorEvent,
        state: RunLifecycle,
    ) -> DispatchDecision:
        audit_record = self._audit_record(action, reason, event, state)
        self._audit_records.append(audit_record)
        self._metrics.increment(f"orchestrator.events.{action.value}")
        logger.info(
            "orchestrator event %s for run %s: %s",
            action.value,
            event.run_id,
            reason,
        )
        return DispatchDecision(
            action=action,
            reason=reason,
            run_id=event.run_id,
            event_type=event.event_type,
            lifecycle_state=event.lifecycle_state,
            committed_lifecycle_state=state.lifecycle_state,
            committed_attempt=state.attempt,
            committed_revision=state.revision,
            audit_record=audit_record,
        )

    def _audit_record(
        self,
        action: DispatchAction,
        reason: str,
        event: OrchestratorEvent,
        state: RunLifecycle,
    ) -> Dict[str, Any]:
        return {
            "action": action.value,
            "reason": reason,
            "run_id": event.run_id,
            "event_type": event.event_type,
            "attempt": event.attempt,
            "revision": event.revision,
            "version": event.version,
            "requested_state": event.lifecycle_state.value,
            "committed_state": state.lifecycle_state.value,
            "committed_attempt": state.attempt,
            "committed_revision": state.revision,
            "target_version": state.upgrading_to,
        }
