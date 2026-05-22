from src.orchestrator.engine import OrchestrationEngine


class TestOrchestratorEventIntake:
    def setup_method(self):
        self.engine = OrchestrationEngine()

    def test_rejects_cross_tenant_event_and_preserves_lifecycle(self):
        accepted = self.engine.ingest_event(
            {
                "run_id": "run-1",
                "tenant_id": "tenant-a",
                "lifecycle": "running",
                "attempt": 1,
                "revision": 1,
            }
        )
        rejected = self.engine.ingest_event(
            {
                "run_id": "run-1",
                "tenant_id": "tenant-b",
                "lifecycle": "completed",
                "attempt": 1,
                "revision": 2,
                "payload": {"private": "do-not-log"},
            }
        )

        assert accepted["accepted"]
        assert not rejected["accepted"]
        assert rejected["reason"] == "tenant_mismatch"
        assert rejected["current_lifecycle"] == "running"
        assert self.engine.event_state("run-1")["lifecycle"] == "running"
        assert "tenant-b" not in str(self.engine.event_audit[-1])
        assert "do-not-log" not in str(self.engine.event_audit[-1])

    def test_rejects_duplicate_or_stale_revision(self):
        self.engine.ingest_event(
            {
                "run_id": "run-2",
                "tenant_id": "tenant-a",
                "lifecycle": "running",
                "attempt": 2,
                "revision": 4,
            }
        )

        rejected = self.engine.ingest_event(
            {
                "run_id": "run-2",
                "tenant_id": "tenant-a",
                "lifecycle": "completed",
                "attempt": 2,
                "revision": 4,
            }
        )

        assert not rejected["accepted"]
        assert rejected["reason"] == "stale_revision"
        assert self.engine.event_state("run-2")["lifecycle"] == "running"

    def test_rejects_terminal_lifecycle_regression(self):
        self.engine.ingest_event(
            {
                "run_id": "run-3",
                "tenant_id": "tenant-a",
                "lifecycle": "running",
                "attempt": 1,
                "revision": 1,
            }
        )
        self.engine.ingest_event(
            {
                "run_id": "run-3",
                "tenant_id": "tenant-a",
                "lifecycle": "completed",
                "attempt": 1,
                "revision": 2,
            }
        )

        rejected = self.engine.ingest_event(
            {
                "run_id": "run-3",
                "tenant_id": "tenant-a",
                "lifecycle": "running",
                "attempt": 1,
                "revision": 3,
            }
        )

        assert not rejected["accepted"]
        assert rejected["reason"] == "invalid_lifecycle_transition"
        assert self.engine.event_state("run-3")["lifecycle"] == "completed"

    def test_records_sanitized_audit_and_metrics_for_intake_decisions(self):
        self.engine.ingest_event(
            {
                "run_id": "run-4",
                "tenant_id": "tenant-a",
                "lifecycle": "queued",
                "attempt": 0,
                "revision": 1,
            }
        )
        self.engine.ingest_event(
            {
                "run_id": "run-4",
                "tenant_id": "tenant-a",
                "lifecycle": "running",
                "attempt": 0,
                "revision": 2,
                "payload": {"token": "secret"},
            }
        )
        self.engine.ingest_event(
            {
                "run_id": "run-4",
                "tenant_id": "tenant-b",
                "lifecycle": "completed",
                "attempt": 0,
                "revision": 3,
            }
        )

        assert self.engine.event_metrics["event_intake.accepted"] == 2
        assert self.engine.event_metrics["event_intake.rejected"] == 1
        assert self.engine.event_metrics[
            "event_intake.rejected.tenant_mismatch"
        ] == 1
        assert "tenant-b" not in str(self.engine.event_audit[-1])
        assert "secret" not in str(self.engine.event_audit)

    def test_rejects_malformed_intake_event_before_state_commit(self):
        rejected = self.engine.ingest_event(
            {
                "run_id": "run-5",
                "tenant_id": "tenant-a",
                "lifecycle": "running",
                "attempt": -1,
                "revision": 1,
            }
        )

        assert not rejected["accepted"]
        assert rejected["reason"] == "invalid_attempt"
        assert self.engine.event_state("run-5") is None
        assert self.engine.event_metrics[
            "event_intake.rejected.invalid_attempt"
        ] == 1
