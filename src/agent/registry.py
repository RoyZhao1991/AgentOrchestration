"""Agent Registry — Manages agent lifecycle and metadata."""

import hashlib
import json
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from src.common.metrics import metrics


class AgentStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    FAILED = "failed"
    TERMINATED = "terminated"


class AgentRegistry:
    def __init__(self, storage_backend: str = "memory"):
        self.storage_backend = storage_backend
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._index: Dict[str, List[str]] = {}
        self._capability_index: Dict[str, List[str]] = {}
        self._schema_cache: Dict[str, Dict[str, Any]] = {}
        self._audit_records: List[Dict[str, Any]] = []

    def register(
        self,
        name: str,
        agent_type: str,
        config: Optional[Dict] = None,
    ) -> str:
        agent_id = str(uuid.uuid4())
        timestamp = time.time()
        config = config or {}
        contracts = self._normalize_capabilities(config)
        self._validate_capability_contracts(agent_id, contracts)

        self._agents[agent_id] = {
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "status": AgentStatus.PENDING.value,
            "config": config,
            "capability_contracts": {
                capability: {
                    "schema": schema,
                    "fingerprint": self._schema_fingerprint(schema),
                }
                for capability, schema in contracts.items()
            },
            "created_at": timestamp,
            "updated_at": timestamp,
            "version": "1.0.0",
            "metrics": {"tasks_completed": 0, "errors": 0, "uptime": 0},
        }
        group = agent_type.split(".")[0]
        if group not in self._index:
            self._index[group] = []
        self._index[group].append(agent_id)
        self._index_capabilities(agent_id, contracts)
        self._record_audit("registered", agent_id, contracts, "schema_cached")
        return agent_id

    def get(self, agent_id: str) -> Optional[Dict[str, Any]]:
        return self._agents.get(agent_id)

    def list(
        self,
        status: Optional[AgentStatus] = None,
        group: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
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
        self._agents[agent_id]["status"] = status.value
        self._agents[agent_id]["updated_at"] = time.time()
        return True

    def update_capabilities(self, agent_id: str, capabilities: Any) -> bool:
        if agent_id not in self._agents:
            return False

        contracts = self._normalize_capabilities({
            "capabilities": capabilities,
        })
        self._validate_capability_contracts(agent_id, contracts)
        self._remove_capability_index(agent_id)
        self._agents[agent_id]["capability_contracts"] = {
            capability: {
                "schema": schema,
                "fingerprint": self._schema_fingerprint(schema),
            }
            for capability, schema in contracts.items()
        }
        self._agents[agent_id]["config"]["capabilities"] = capabilities
        self._agents[agent_id]["updated_at"] = time.time()
        self._index_capabilities(agent_id, contracts)
        self._record_audit(
            "schema_cache_invalidated",
            agent_id,
            contracts,
            "capability_contract_changed",
        )
        metrics.increment("registry.schema_cache.invalidated")
        return True

    def resolve_capability(
        self,
        capability: str,
        required_schema: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        cache_entry = self._rebuild_schema_cache(capability)
        if not cache_entry:
            return None

        required_fingerprint = None
        if required_schema is not None:
            required_fingerprint = self._schema_fingerprint(required_schema)
        if (
            required_fingerprint is not None
            and cache_entry["fingerprint"] != required_fingerprint
        ):
            self._audit_records.append({
                "action": "resolution_rejected",
                "reason": "required_schema_fingerprint_mismatch",
                "capability": capability,
                "expected_fingerprint": required_fingerprint,
                "cached_fingerprint": cache_entry["fingerprint"],
                "recorded_at": time.time(),
            })
            metrics.increment("registry.schema_cache.rejected")
            raise ValueError("Capability contract does not match request")

        for candidate_id in cache_entry["agent_ids"]:
            agent = self._agents.get(candidate_id)
            if agent:
                return agent
        return None

    def delete(self, agent_id: str) -> bool:
        if agent_id not in self._agents:
            return False
        agent = self._agents.pop(agent_id)
        group = agent["type"].split(".")[0]
        if group in self._index and agent_id in self._index[group]:
            self._index[group].remove(agent_id)
        self._remove_capability_index(agent_id)
        return True

    def count(self) -> int:
        return len(self._agents)

    def audit_records(self) -> Tuple[Dict[str, Any], ...]:
        return tuple(dict(record) for record in self._audit_records)

    def _normalize_capabilities(
        self,
        config: Dict[str, Any],
    ) -> Dict[str, Any]:
        capabilities = config.get("capabilities", {})
        if not capabilities:
            return {}
        if isinstance(capabilities, dict):
            return dict(capabilities)
        if isinstance(capabilities, list):
            normalized = {}
            for item in capabilities:
                if not isinstance(item, dict) or "name" not in item:
                    raise ValueError("Capability entries must include a name")
                schema = item.get("schema", item.get("contract", {}))
                normalized[item["name"]] = schema
            return normalized
        raise ValueError("Capabilities must be a mapping or list")

    def _validate_capability_contracts(
        self,
        agent_id: str,
        contracts: Dict[str, Any],
    ) -> None:
        for capability, schema in contracts.items():
            fingerprint = self._schema_fingerprint(schema)
            cache_entry = self._schema_cache.get(capability)
            if not cache_entry:
                continue

            same_agent_update = agent_id in cache_entry["agent_ids"]
            if cache_entry["fingerprint"] == fingerprint or same_agent_update:
                continue

            self._audit_records.append({
                "action": "registration_rejected",
                "reason": "active_capability_contract_conflict",
                "capability": capability,
                "agent_id": agent_id,
                "cached_fingerprint": cache_entry["fingerprint"],
                "incoming_fingerprint": fingerprint,
                "recorded_at": time.time(),
            })
            metrics.increment("registry.schema_cache.rejected")
            raise ValueError("Active capability contract conflict")

    def _index_capabilities(
        self,
        agent_id: str,
        contracts: Dict[str, Any],
    ) -> None:
        for capability in contracts:
            self._capability_index.setdefault(capability, []).append(agent_id)
            self._rebuild_schema_cache(capability)

    def _remove_capability_index(self, agent_id: str) -> None:
        affected = []
        for capability, agent_ids in self._capability_index.items():
            if agent_id in agent_ids:
                affected.append(capability)
        for capability in affected:
            self._capability_index[capability] = [
                candidate_id
                for candidate_id in self._capability_index[capability]
                if candidate_id != agent_id
            ]
            self._rebuild_schema_cache(capability)

    def _rebuild_schema_cache(
        self,
        capability: str,
    ) -> Optional[Dict[str, Any]]:
        agent_ids = [
            agent_id
            for agent_id in self._capability_index.get(capability, [])
            if agent_id in self._agents
        ]
        fingerprints = {}
        for agent_id in agent_ids:
            contract = self._agents[agent_id]["capability_contracts"].get(
                capability,
            )
            if contract:
                fingerprints.setdefault(
                    contract["fingerprint"],
                    [],
                ).append(agent_id)

        if not fingerprints:
            self._schema_cache.pop(capability, None)
            return None

        if len(fingerprints) > 1:
            self._schema_cache.pop(capability, None)
            self._audit_records.append({
                "action": "cache_rebuild_rejected",
                "reason": "multiple_active_contracts",
                "capability": capability,
                "fingerprint_count": len(fingerprints),
                "recorded_at": time.time(),
            })
            metrics.increment("registry.schema_cache.rejected")
            raise ValueError("Multiple active capability contracts")

        fingerprint, cached_agent_ids = next(iter(fingerprints.items()))
        cache_entry = {
            "capability": capability,
            "fingerprint": fingerprint,
            "agent_ids": tuple(cached_agent_ids),
            "cached_at": time.time(),
        }
        self._schema_cache[capability] = cache_entry
        return cache_entry

    def _record_audit(
        self,
        action: str,
        agent_id: str,
        contracts: Dict[str, Any],
        reason: str,
    ) -> None:
        for capability, schema in contracts.items():
            self._audit_records.append({
                "action": action,
                "reason": reason,
                "agent_id": agent_id,
                "capability": capability,
                "fingerprint": self._schema_fingerprint(schema),
                "recorded_at": time.time(),
            })

    def _schema_fingerprint(self, schema: Any) -> str:
        payload = json.dumps(
            schema,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

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
