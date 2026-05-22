"""Agent Registry — Manages agent lifecycle and metadata."""

import copy
import logging
import time
import uuid
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple


logger = logging.getLogger(__name__)


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
        self._resolution_cache: Dict[
            Tuple[str, Tuple[Tuple[str, Decimal], ...]],
            List[Dict[str, Any]],
        ] = {}
        self._route_cache_targets: Dict[
            Tuple[str, Tuple[Tuple[str, Decimal], ...]],
            Set[str],
        ] = {}
        self._audit_records: List[Dict[str, Any]] = []

    def register(
        self,
        name: str,
        agent_type: str,
        config: Optional[Dict] = None,
    ) -> str:
        safe_config = copy.deepcopy(config or {})
        route_policy = self._route_policy_from_config(safe_config)
        if route_policy is not None:
            self._parse_route_policy(route_policy, agent_type)

        agent_id = str(uuid.uuid4())
        timestamp = time.time()
        self._agents[agent_id] = {
            "id": agent_id,
            "name": name,
            "type": agent_type,
            "status": AgentStatus.PENDING.value,
            "config": safe_config,
            "created_at": timestamp,
            "updated_at": timestamp,
            "version": "1.0.0",
            "metrics": {"tasks_completed": 0, "errors": 0, "uptime": 0},
        }
        group = agent_type.split(".")[0]
        if group not in self._index:
            self._index[group] = []
        self._index[group].append(agent_id)
        self._invalidate_resolution_cache(agent_type)
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
        self._invalidate_resolution_cache(self._agents[agent_id]["type"])
        return True

    def delete(self, agent_id: str) -> bool:
        if agent_id not in self._agents:
            return False
        agent = self._agents.pop(agent_id)
        group = agent["type"].split(".")[0]
        if group in self._index and agent_id in self._index[group]:
            self._index[group].remove(agent_id)
        self._invalidate_resolution_cache(agent["type"])
        return True

    def count(self) -> int:
        return len(self._agents)

    def resolve(
        self,
        agent_type: str,
        route_policy: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        routes = self._parse_route_policy(route_policy, agent_type)
        cache_key = self._route_cache_key(agent_type, routes)
        if cache_key in self._resolution_cache:
            return copy.deepcopy(self._resolution_cache[cache_key])

        targets = routes or ((agent_type, Decimal("100")),)
        resolved: List[Dict[str, Any]] = []
        for target_type, weight in targets:
            matches = [
                agent
                for agent in self._agents.values()
                if agent["type"] == target_type
                and agent["status"] == AgentStatus.RUNNING.value
            ]
            if not matches:
                self._audit_route_decision(
                    agent_type,
                    "deferred",
                    "no_running_agents_for_route",
                    target_type=target_type,
                )
                raise ValueError(
                    f"no running agents available for route {target_type!r}",
                )
            for agent in matches:
                routed_agent = copy.deepcopy(agent)
                routed_agent["route_weight"] = int(weight)
                resolved.append(routed_agent)

        self._resolution_cache[cache_key] = copy.deepcopy(resolved)
        self._route_cache_targets[cache_key] = {
            target for target, _ in targets
        }
        self._audit_route_decision(
            agent_type,
            "accepted",
            "route_policy_resolved",
            route_count=len(targets),
        )
        return resolved

    def audit_records(self) -> List[Dict[str, Any]]:
        return copy.deepcopy(self._audit_records)

    def _route_policy_from_config(
        self,
        config: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        for key in ("routing_policy", "traffic_split", "route_weights"):
            if key in config:
                return {key: config[key]}
        return None

    def _parse_route_policy(
        self,
        route_policy: Optional[Dict[str, Any]],
        agent_type: str,
    ) -> Tuple[Tuple[str, Decimal], ...]:
        if route_policy is None:
            return ()
        routes = self._route_entries(route_policy)
        if not routes:
            return ()

        parsed: List[Tuple[str, Decimal]] = []
        seen_targets: Set[str] = set()
        total = Decimal("0")
        for route in routes:
            raw_target = route["target"]
            if raw_target is None:
                self._audit_route_decision(
                    agent_type,
                    "rejected",
                    "missing_route_target",
                )
                raise ValueError("route target must not be blank")
            target = str(raw_target).strip()
            weight = self._route_weight(route["weight"])
            if not target:
                self._audit_route_decision(
                    agent_type,
                    "rejected",
                    "blank_route_target",
                )
                raise ValueError("route target must not be blank")
            if target in seen_targets:
                self._audit_route_decision(
                    agent_type,
                    "rejected",
                    "duplicate_route_target",
                    target_type=target,
                )
                raise ValueError(f"duplicate route target {target!r}")
            if weight <= 0:
                self._audit_route_decision(
                    agent_type,
                    "rejected",
                    "non_positive_route_weight",
                    target_type=target,
                )
                raise ValueError("route weights must be positive")

            seen_targets.add(target)
            parsed.append((target, weight))
            total += weight

        if total != Decimal("100"):
            self._audit_route_decision(
                agent_type,
                "rejected",
                "route_weight_total_mismatch",
                weight_total=str(total),
                route_count=len(parsed),
            )
            raise ValueError("route weights must total 100")

        return tuple(parsed)

    def _route_entries(
        self,
        route_policy: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        policy = route_policy.get("routing_policy", route_policy)
        if isinstance(policy, dict) and "traffic_split" in policy:
            policy = policy["traffic_split"]
        elif isinstance(policy, dict) and "route_weights" in policy:
            policy = policy["route_weights"]

        if isinstance(policy, dict) and "routes" in policy:
            policy = policy["routes"]

        if isinstance(policy, dict):
            return [
                {"target": target, "weight": weight}
                for target, weight in policy.items()
                if target != "strategy"
            ]

        if isinstance(policy, list):
            entries = []
            for route in policy:
                if not isinstance(route, dict):
                    raise ValueError("route entries must be objects")
                target = (
                    route.get("type")
                    or route.get("agent_type")
                    or route.get("handler")
                    or route.get("target")
                )
                entries.append({
                    "target": target,
                    "weight": route.get("weight"),
                })
            return entries

        raise ValueError("route policy must be a mapping or list")

    def _route_weight(self, value: Any) -> Decimal:
        if isinstance(value, bool):
            raise ValueError("route weights must be numeric")
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError):
            raise ValueError("route weights must be numeric") from None

    def _route_cache_key(
        self,
        agent_type: str,
        routes: Tuple[Tuple[str, Decimal], ...],
    ) -> Tuple[str, Tuple[Tuple[str, Decimal], ...]]:
        return agent_type, routes

    def _invalidate_resolution_cache(self, agent_type: str) -> None:
        stale_keys = [
            key
            for key, targets in self._route_cache_targets.items()
            if agent_type in targets or key[0] == agent_type
        ]
        for key in stale_keys:
            self._resolution_cache.pop(key, None)
            self._route_cache_targets.pop(key, None)

    def _audit_route_decision(
        self,
        agent_type: str,
        decision: str,
        reason: str,
        **details: Any,
    ) -> None:
        record = {
            "event": "registry_route_policy",
            "agent_type": agent_type,
            "decision": decision,
            "reason": reason,
            "details": details,
            "timestamp": time.time(),
        }
        self._audit_records.append(record)
        logger.info(
            "registry route policy %s for %s: %s",
            decision,
            agent_type,
            reason,
        )

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
