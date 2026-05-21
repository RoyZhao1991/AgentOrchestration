"""Agent Registry — Manages agent lifecycle and metadata."""

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class AgentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"
    TERMINATED = "terminated"


class AgentRegistry:
    SUPPORTED_PROTOCOL_VERSION = "1.0.0"
    TERMINAL_STATUSES = {
        AgentStatus.STOPPED,
        AgentStatus.FAILED,
        AgentStatus.TERMINATED,
    }
    ALLOWED_STATUS_TRANSITIONS = {
        AgentStatus.PENDING: {
            AgentStatus.RUNNING,
            AgentStatus.PAUSED,
            AgentStatus.STOPPED,
            AgentStatus.FAILED,
            AgentStatus.TERMINATED,
        },
        AgentStatus.RUNNING: {
            AgentStatus.PAUSED,
            AgentStatus.STOPPED,
            AgentStatus.FAILED,
            AgentStatus.TERMINATED,
        },
        AgentStatus.PAUSED: {
            AgentStatus.RUNNING,
            AgentStatus.STOPPED,
            AgentStatus.FAILED,
            AgentStatus.TERMINATED,
        },
        AgentStatus.STOPPED: set(),
        AgentStatus.FAILED: {AgentStatus.TERMINATED},
        AgentStatus.TERMINATED: set(),
    }

    def __init__(self, storage_backend: str = "memory"):
        self.storage_backend = storage_backend
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._index: Dict[str, List[str]] = {}
        self._resolution_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._audit_log: List[Dict[str, Any]] = []

    def register(self, name: str, agent_type: str, config: Optional[Dict] = None) -> str:
        config = config or {}
        protocol_version = str(
            config.get("protocol_version", self.SUPPORTED_PROTOCOL_VERSION),
        )
        if not self._is_protocol_compatible(
            offered_version=protocol_version,
            requested_version=self.SUPPORTED_PROTOCOL_VERSION,
        ):
            self._audit_protocol_rejection(
                agent_id=None,
                offered_version=protocol_version,
                requested_version=self.SUPPORTED_PROTOCOL_VERSION,
                reason="unsupported_protocol_version",
            )
            raise ValueError(f"Unsupported protocol version: {protocol_version}")

        agent_id = str(uuid.uuid4())
        timestamp = time.time()
        self._agents[agent_id] = {
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "status": AgentStatus.PENDING.value,
            "config": config,
            "created_at": timestamp,
            "updated_at": timestamp,
            "version": protocol_version,
            "protocol_version": protocol_version,
            "protocol_generation": 1,
            "metrics": {"tasks_completed": 0, "errors": 0, "uptime": 0},
        }
        group = agent_type.split(".")[0]
        if group not in self._index:
            self._index[group] = []
        self._index[group].append(agent_id)
        return agent_id

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self._agents.get(agent_id)

    def resolve(
        self,
        agent_id: str,
        requested_protocol_version: str = SUPPORTED_PROTOCOL_VERSION,
    ) -> Optional[Dict[str, Any]]:
        cache_key = (agent_id, requested_protocol_version)
        if cache_key in self._resolution_cache:
            return self._resolution_cache[cache_key]

        agent = self._agents.get(agent_id)
        if not agent:
            return None

        offered_version = agent["protocol_version"]
        if not self._is_protocol_compatible(
            offered_version=offered_version,
            requested_version=requested_protocol_version,
        ):
            self._audit_protocol_rejection(
                agent_id=agent_id,
                offered_version=offered_version,
                requested_version=requested_protocol_version,
                reason="incompatible_protocol_upgrade",
            )
            return None

        if AgentStatus(agent["status"]) in self.TERMINAL_STATUSES:
            self._audit_protocol_rejection(
                agent_id=agent_id,
                offered_version=offered_version,
                requested_version=requested_protocol_version,
                reason="agent_not_available",
            )
            return None

        self._resolution_cache[cache_key] = agent
        return agent

    def list(self, status: Optional[AgentStatus] = None, group: Optional[str] = None) -> List[Dict[str, Any]]:
        agents = self._agents.values()
        if status:
            agents = [a for a in agents if a["status"] == status.value]
        if group:
            agent_ids = self._index.get(group, [])
            agents = [a for a in agents if a["id"] in agent_ids]
        return list(agents)

    def update_status(self, agent_id: str, status: AgentStatus) -> bool:
        if agent_id not in self._agents:
            return False
        current_status = AgentStatus(self._agents[agent_id]["status"])
        allowed_next_statuses = self.ALLOWED_STATUS_TRANSITIONS[current_status]
        if status not in allowed_next_statuses:
            self._audit_protocol_rejection(
                agent_id=agent_id,
                offered_version=self._agents[agent_id]["protocol_version"],
                requested_version=self.SUPPORTED_PROTOCOL_VERSION,
                reason="invalid_lifecycle_transition",
            )
            return False

        self._agents[agent_id]["status"] = status.value
        self._agents[agent_id]["updated_at"] = time.time()
        self._invalidate_resolution_cache(agent_id)
        return True

    def negotiate_protocol_upgrade(
        self,
        agent_id: str,
        requested_protocol_version: str,
    ) -> bool:
        if agent_id not in self._agents:
            return False

        agent = self._agents[agent_id]
        current_protocol_version = agent["protocol_version"]
        if requested_protocol_version == current_protocol_version:
            self._audit_protocol_rejection(
                agent_id=agent_id,
                offered_version=current_protocol_version,
                requested_version=requested_protocol_version,
                reason="duplicate_protocol_upgrade",
            )
            return False

        if AgentStatus(agent["status"]) in {AgentStatus.RUNNING, AgentStatus.PAUSED}:
            self._audit_protocol_rejection(
                agent_id=agent_id,
                offered_version=current_protocol_version,
                requested_version=requested_protocol_version,
                reason="lifecycle_state_not_stable",
            )
            return False

        if not self._is_protocol_upgrade_allowed(
            current_version=current_protocol_version,
            requested_version=requested_protocol_version,
        ):
            self._audit_protocol_rejection(
                agent_id=agent_id,
                offered_version=current_protocol_version,
                requested_version=requested_protocol_version,
                reason="incompatible_protocol_upgrade",
            )
            return False

        agent["version"] = requested_protocol_version
        agent["protocol_version"] = requested_protocol_version
        agent["protocol_generation"] += 1
        agent["updated_at"] = time.time()
        self._invalidate_resolution_cache(agent_id)
        return True

    def delete(self, agent_id: str) -> bool:
        if agent_id not in self._agents:
            return False
        agent = self._agents.pop(agent_id)
        group = agent["type"].split(".")[0]
        if group in self._index and agent_id in self._index[group]:
            self._index[group].remove(agent_id)
        self._invalidate_resolution_cache(agent_id)
        return True

    def count(self) -> int:
        return len(self._agents)

    def audit_log(self) -> List[Dict[str, Any]]:
        return list(self._audit_log)

    def _invalidate_resolution_cache(self, agent_id: str) -> None:
        self._resolution_cache = {
            key: value
            for key, value in self._resolution_cache.items()
            if key[0] != agent_id
        }

    def _audit_protocol_rejection(
        self,
        agent_id: Optional[str],
        offered_version: str,
        requested_version: str,
        reason: str,
    ) -> None:
        self._audit_log.append(
            {
                "event": "registry_protocol_rejected",
                "agent_id": agent_id,
                "offered_version": offered_version,
                "requested_version": requested_version,
                "reason": reason,
                "created_at": time.time(),
            },
        )

    def _is_protocol_compatible(
        self,
        offered_version: str,
        requested_version: str,
    ) -> bool:
        offered = self._parse_protocol_version(offered_version)
        requested = self._parse_protocol_version(requested_version)
        if not offered or not requested:
            return False
        offered_major, offered_minor, _ = offered
        requested_major, requested_minor, _ = requested
        return offered_major == requested_major and offered_minor >= requested_minor

    def _is_protocol_upgrade_allowed(
        self,
        current_version: str,
        requested_version: str,
    ) -> bool:
        current = self._parse_protocol_version(current_version)
        requested = self._parse_protocol_version(requested_version)
        if not current or not requested:
            return False

        current_major, current_minor, _ = current
        requested_major, requested_minor, _ = requested
        return current_major == requested_major and requested_minor >= current_minor

    def _parse_protocol_version(self, version: str) -> Optional[Tuple[int, int, int]]:
        parts = version.split(".")
        if len(parts) != 3:
            return None
        try:
            major, minor, patch = (int(part) for part in parts)
            return major, minor, patch
        except ValueError:
            return None

# 2019-01-29T11:24:49 update

# 2019-04-09T13:38:38 update

# 2019-04-11T11:24:12 update

# 2019-06-26T17:03:48 update

# 2019-07-03T14:55:48 update

# 2019-07-18T18:18:47 update

# 2019-11-05T11:27:19 update

# 2019-11-20T11:35:05 update

# 2019-11-23T15:28:54 update

# 2020-03-13T09:23:07 update

# 2020-03-30T19:31:18 update

# 2020-04-22T15:03:30 update

# 2020-07-21T10:00:48 update

# 2020-09-10T09:02:08 update

# 2020-09-10T13:39:12 update

# 2020-09-22T16:27:52 update

# 2020-10-15T10:33:14 update

# 2021-05-13T11:15:56 update

# 2021-07-07T14:57:13 update

# 2021-07-13T15:15:19 update

# 2021-07-27T10:18:16 update

# 2022-03-11T15:24:11 update

# 2022-09-22T13:24:20 update

# 2022-11-01T12:20:40 update

# 2023-01-30T12:32:27 update

# 2023-03-10T09:43:50 update

# 2023-05-10T14:28:01 update

# 2023-05-11T20:04:46 update

# 2023-05-30T17:00:59 update

# 2023-07-13T17:54:32 update

# 2023-07-20T19:04:20 update

# 2023-07-31T17:00:02 update

# 2023-09-05T19:42:07 update

# 2024-01-02T10:29:47 update

# 2024-09-17T12:45:29 update

# 2024-09-17T11:51:01 update

# 2024-11-06T18:20:15 update

# 2025-01-12T15:13:14 update

# 2025-01-14T20:24:39 update

# 2025-03-26T20:21:27 update

# 2025-04-10T18:27:06 update

# 2025-06-19T20:34:58 update

# 2025-06-21T20:23:53 update

# 2025-06-24T20:30:30 update

# 2025-07-03T13:28:03 update

# 2025-07-24T17:42:21 update

# 2025-08-19T17:42:23 update

# 2025-08-21T11:06:52 update

# 2025-10-24T09:10:08 update

# 2025-12-18T19:34:38 update

# 2026-02-06T11:22:22 update

# 2026-02-13T15:42:04 update

# 2026-04-10T08:16:30 update

# 2026-04-29T18:16:11 update
