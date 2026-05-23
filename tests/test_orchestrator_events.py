from src.common.metrics import MetricsCollector
from src.orchestrator.events import (
    DispatchAction,
    LifecycleState,
    OrchestratorEvent,
    OrchestratorEventDispatcher,
)


def event(
    event_type="task.started",
    state=LifecycleState.RUNNING,
    attempt=1,
    revision=1,
    version="2.0.0",
    payload=None,
):
    return OrchestratorEvent(
        run_id="run-1",
        event_type=event_type,
        lifecycle_state=state,
        attempt=attempt,
        revision=revision,
        version=version,
        payload=payload or {},
    )


def test_rolling_upgrade_defers_version_mismatch_and_preserves_state():
    dispatcher = OrchestratorEventDispatcher()
    dispatcher.start_rolling_upgrade("run-1", target_version="2.0.0")

    decision = dispatcher.dispatch(event(version="1.0.0"))

    assert decision.action == DispatchAction.DEFERRED
    assert decision.deferred
    assert decision.reason == "version_mismatch_during_rolling_upgrade"
    assert decision.committed_lifecycle_state == LifecycleState.PENDING
    assert dispatcher.get_state("run-1").lifecycle_state == (
        LifecycleState.PENDING
    )
    assert dispatcher.deferred_events("run-1")[0].event_type == "task.started"


def test_rolling_upgrade_commits_matching_version_transition():
    dispatcher = OrchestratorEventDispatcher()
    dispatcher.start_rolling_upgrade("run-1", target_version="2.0.0")

    decision = dispatcher.dispatch(event(version="2.0.0"))

    assert decision.committed
    state = dispatcher.get_state("run-1")
    assert state.lifecycle_state == LifecycleState.RUNNING
    assert state.revision == 1
    assert state.upgrading_to == "2.0.0"


def test_finish_rolling_upgrade_promotes_target_version():
    dispatcher = OrchestratorEventDispatcher()
    dispatcher.start_rolling_upgrade("run-1", target_version="2.0.0")

    state = dispatcher.finish_rolling_upgrade("run-1")
    unchanged = dispatcher.finish_rolling_upgrade("run-1")

    assert state.version == "2.0.0"
    assert state.upgrading_to is None
    assert unchanged == state


def test_unknown_event_type_is_quarantined_without_state_change():
    dispatcher = OrchestratorEventDispatcher()

    decision = dispatcher.dispatch(event(event_type="plugin.surprise"))

    assert decision.quarantined
    assert decision.reason == "unknown_event_type"
    assert dispatcher.get_state("run-1").lifecycle_state == (
        LifecycleState.PENDING
    )
    assert dispatcher.quarantined_events("run-1")[0].event_type == (
        "plugin.surprise"
    )


def test_stale_attempt_and_duplicate_revision_are_quarantined():
    dispatcher = OrchestratorEventDispatcher()
    assert dispatcher.dispatch(event()).committed

    stale_attempt = dispatcher.dispatch(
        event(
            event_type="task.completed",
            state=LifecycleState.COMPLETED,
            attempt=0,
            revision=2,
        )
    )
    duplicate_revision = dispatcher.dispatch(
        event(
            event_type="task.completed",
            state=LifecycleState.COMPLETED,
            attempt=1,
            revision=1,
        )
    )

    assert stale_attempt.reason == "stale_attempt"
    assert duplicate_revision.reason == "stale_or_duplicate_revision"
    assert dispatcher.get_state("run-1").lifecycle_state == (
        LifecycleState.RUNNING
    )
    assert len(dispatcher.quarantined_events("run-1")) == 2


def test_invalid_lifecycle_transition_is_quarantined():
    dispatcher = OrchestratorEventDispatcher()
    assert dispatcher.dispatch(event()).committed
    assert dispatcher.dispatch(
        event(
            event_type="task.completed",
            state=LifecycleState.COMPLETED,
            revision=2,
        )
    ).committed

    decision = dispatcher.dispatch(
        event(
            event_type="task.started",
            state=LifecycleState.RUNNING,
            revision=3,
        )
    )

    assert decision.reason == "invalid_lifecycle_transition"
    assert decision.quarantined
    assert dispatcher.get_state("run-1").lifecycle_state == (
        LifecycleState.COMPLETED
    )


def test_audit_record_explains_decision_without_private_payload_data():
    dispatcher = OrchestratorEventDispatcher()
    private_payload = {
        "token": "secret-token",
        "hidden_prompt": "do not serialize",
    }

    decision = dispatcher.dispatch(
        event(event_type="handler.unknown", payload=private_payload)
    )

    audit_text = str(decision.audit_record)
    assert "unknown_event_type" in audit_text
    assert "secret-token" not in audit_text
    assert "hidden_prompt" not in audit_text
    assert "payload" not in decision.audit_record
    assert dispatcher.audit_records() == (decision.audit_record,)


def test_metrics_count_committed_deferred_and_quarantined_decisions():
    metrics = MetricsCollector()
    dispatcher = OrchestratorEventDispatcher(metrics_collector=metrics)
    dispatcher.start_rolling_upgrade("run-1", target_version="2.0.0")

    dispatcher.dispatch(event(version="1.0.0"))
    dispatcher.dispatch(event(version="2.0.0"))
    dispatcher.dispatch(event(event_type="plugin.surprise", revision=2))

    counters = metrics.snapshot()["counters"]
    assert counters["orchestrator.events.deferred"] == 1
    assert counters["orchestrator.events.committed"] == 1
    assert counters["orchestrator.events.quarantined"] == 1
