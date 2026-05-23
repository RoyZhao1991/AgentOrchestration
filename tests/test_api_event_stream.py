from fastapi.testclient import TestClient

from src.api.event_stream import run_event_store
from src.api.server import create_app


class TestRunEventsAPI:
    def setup_method(self):
        run_event_store.clear()
        run_event_store.seed(
            "run-1",
            "workspace-a",
            [
                {"sequence": 0, "type": "queued"},
                {"sequence": 1, "type": "started"},
                {"sequence": 2, "type": "completed"},
            ],
        )
        self.client = TestClient(create_app())
        self.headers = {
            "Authorization": "Bearer test-token",
            "X-Workspace-ID": "workspace-a",
        }

    def test_authorized_run_events_are_paginated(self):
        response = self.client.get(
            "/api/v2/runs/run-1/events?start=0&end=3&limit=2",
            headers=self.headers,
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["start"] == 0
        assert payload["end"] == 3
        assert [event["type"] for event in payload["events"]] == [
            "queued",
            "started",
        ]
        assert run_event_store.lookup_count == 1

    def test_missing_bearer_is_rejected_before_lookup(self):
        response = self.client.get(
            "/api/v2/runs/run-1/events?start=0&end=3&limit=2",
            headers={"X-Workspace-ID": "workspace-a"},
        )

        assert response.status_code == 401
        assert run_event_store.lookup_count == 0

    def test_missing_workspace_is_rejected_before_lookup(self):
        response = self.client.get(
            "/api/v2/runs/run-1/events?start=0&end=3&limit=2",
            headers={"Authorization": "Bearer test-token"},
        )

        assert response.status_code == 401
        assert response.json()["detail"] == "Workspace header required"
        assert run_event_store.lookup_count == 0

    def test_oversized_window_is_rejected_before_lookup(self):
        response = self.client.get(
            "/api/v2/runs/run-1/events?start=0&end=5001&limit=100",
            headers=self.headers,
        )

        assert response.status_code == 422
        assert "pagination window" in response.json()["detail"]
        assert run_event_store.lookup_count == 0

    def test_cross_workspace_access_is_rejected(self):
        response = self.client.get(
            "/api/v2/runs/run-1/events?start=0&end=3&limit=2",
            headers={
                "Authorization": "Bearer test-token",
                "X-Workspace-ID": "workspace-b",
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "Run not in workspace"
        assert run_event_store.lookup_count == 1
