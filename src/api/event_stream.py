"""Shared event stream service guards."""

from typing import Any, Dict, List, Optional

from fastapi import HTTPException

MAX_EVENT_LIMIT = 500
MAX_EVENT_WINDOW = 5000


class RunEventStore:
    def __init__(self):
        self._runs: Dict[str, Dict[str, Any]] = {}
        self.lookup_count = 0

    def clear(self) -> None:
        self._runs.clear()
        self.lookup_count = 0

    def seed(
        self,
        run_id: str,
        workspace_id: str,
        events: List[Dict[str, Any]],
    ) -> None:
        self._runs[run_id] = {
            "workspace_id": workspace_id,
            "events": list(events),
        }

    def get_run_events(self, run_id: str) -> Optional[Dict[str, Any]]:
        self.lookup_count += 1
        return self._runs.get(run_id)


class RunEventService:
    def __init__(self, store: RunEventStore):
        self.store = store

    def list_run_events(
        self,
        run_id: str,
        workspace_id: Optional[str],
        start: int,
        end: int,
        limit: int,
    ) -> Dict[str, Any]:
        workspace = (workspace_id or "").strip()
        if not workspace:
            raise HTTPException(
                status_code=401,
                detail="Workspace header required",
            )

        self._validate_window(start, end, limit)

        record = self.store.get_run_events(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if record["workspace_id"] != workspace:
            raise HTTPException(status_code=403, detail="Run not in workspace")

        windowed_events = self._window_events(record["events"], start, end)
        return {
            "run_id": run_id,
            "start": start,
            "end": end,
            "limit": limit,
            "events": windowed_events[:limit],
        }

    def _validate_window(self, start: int, end: int, limit: int) -> None:
        if start < 0:
            raise HTTPException(
                status_code=422,
                detail="start must be greater than or equal to zero",
            )
        if end < start:
            raise HTTPException(
                status_code=422,
                detail="end must be greater than or equal to start",
            )
        if end - start > MAX_EVENT_WINDOW:
            raise HTTPException(
                status_code=422,
                detail="pagination window exceeds 5000 events",
            )
        if limit < 1 or limit > MAX_EVENT_LIMIT:
            raise HTTPException(
                status_code=422,
                detail="limit must be between 1 and 500",
            )

    def _window_events(
        self,
        events: List[Dict[str, Any]],
        start: int,
        end: int,
    ) -> List[Dict[str, Any]]:
        windowed_events = []
        for index, event in enumerate(events):
            sequence = int(event.get("sequence", index))
            if start <= sequence < end:
                windowed_events.append(event)
        return windowed_events


run_event_store = RunEventStore()
run_event_service = RunEventService(run_event_store)
