"""Webhook subscription filter validation and delivery registry."""

from __future__ import annotations

import time
from collections.abc import Iterable
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4


ALLOWED_FILTER_FIELDS = {"agent_id", "event_type", "source", "status"}


class WebhookFilterError(ValueError):
    pass


class WebhookRegistry:
    def __init__(self):
        self._subscriptions: Dict[str, Dict[str, Any]] = {}
        self._deliveries: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

    def clear(self) -> None:
        self._subscriptions.clear()
        self._deliveries.clear()

    def register_subscription(
        self,
        workspace_id: str,
        callback_url: str,
        filters: Dict[str, Any],
    ) -> Dict[str, Any]:
        self._validate_callback_url(callback_url)

        validated_filters = self._validate_filters(filters)
        subscription_id = str(uuid4())
        subscription = {
            "id": subscription_id,
            "workspace_id": workspace_id,
            "callback_url": callback_url,
            "filters": validated_filters,
            "active": True,
            "version": 1,
            "created_at": time.time(),
        }
        self._subscriptions[subscription_id] = subscription
        return self._public_subscription(subscription)

    def disable_subscription(
        self,
        workspace_id: str,
        subscription_id: str,
    ) -> bool:
        subscription = self._subscriptions.get(subscription_id)
        if not subscription or subscription["workspace_id"] != workspace_id:
            return False
        subscription["active"] = False
        subscription["version"] += 1
        return True

    def update_subscription(
        self,
        workspace_id: str,
        subscription_id: str,
        callback_url: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        subscription = self._subscriptions.get(subscription_id)
        if not subscription or subscription["workspace_id"] != workspace_id:
            return None

        if callback_url is not None:
            self._validate_callback_url(callback_url)
            subscription["callback_url"] = callback_url

        if filters is not None:
            subscription["filters"] = self._validate_filters(filters)

        subscription["version"] += 1
        subscription["active"] = True
        return self._public_subscription(subscription)

    def deliver_event(
        self,
        workspace_id: str,
        delivery_id: str,
        event: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        key = (workspace_id, delivery_id)
        if key in self._deliveries:
            return [dict(record) for record in self._deliveries[key]]

        records = []
        for subscription in self._subscriptions.values():
            if subscription["workspace_id"] != workspace_id:
                continue
            if not subscription["active"]:
                continue
            if not self._matches(subscription["filters"], event):
                continue
            records.append(
                self._delivery_record(
                    workspace_id,
                    delivery_id,
                    event,
                    "queued",
                    subscription_id=subscription["id"],
                )
            )

        if not records:
            records.append(
                self._delivery_record(
                    workspace_id,
                    delivery_id,
                    event,
                    "rejected",
                    reason="no_matching_subscription",
                )
            )

        self._deliveries[key] = records
        return [dict(record) for record in records]

    def retry_delivery(
        self,
        workspace_id: str,
        delivery_id: str,
    ) -> List[Dict[str, Any]]:
        key = (workspace_id, delivery_id)
        records = self._deliveries.get(key)
        if records is None:
            return [
                {
                    "delivery_id": delivery_id,
                    "workspace_id": workspace_id,
                    "status": "rejected",
                    "reason": "unknown_delivery",
                    "attempts": 0,
                }
            ]

        return [dict(record) for record in records]

    def _validate_filters(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(filters, dict):
            raise WebhookFilterError("filters must be an object")

        unknown = sorted(set(filters) - ALLOWED_FILTER_FIELDS)
        if unknown:
            raise WebhookFilterError(
                f"unsupported filter fields: {', '.join(unknown)}"
            )
        return deepcopy(filters)

    @staticmethod
    def _validate_callback_url(callback_url: str) -> None:
        if not callback_url.startswith(("https://", "http://")):
            raise WebhookFilterError("callback_url must be http or https")

    def _matches(self, filters: Dict[str, Any], event: Dict[str, Any]) -> bool:
        for field, expected in filters.items():
            is_collection = isinstance(expected, Iterable)
            if is_collection and not isinstance(expected, str):
                if event.get(field) not in expected:
                    return False
            elif event.get(field) != expected:
                return False
        return True

    def _delivery_record(
        self,
        workspace_id: str,
        delivery_id: str,
        event: Dict[str, Any],
        status: str,
        subscription_id: str = None,
        reason: str = None,
    ) -> Dict[str, Any]:
        record = {
            "delivery_id": delivery_id,
            "workspace_id": workspace_id,
            "event_type": event.get("event_type"),
            "status": status,
            "attempts": 1 if status == "queued" else 0,
        }
        if subscription_id:
            record["subscription_id"] = subscription_id
        if reason:
            record["reason"] = reason
        return record

    @staticmethod
    def _public_subscription(subscription: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": subscription["id"],
            "workspace_id": subscription["workspace_id"],
            "filters": deepcopy(subscription["filters"]),
            "active": subscription["active"],
            "version": subscription["version"],
        }


webhook_registry = WebhookRegistry()
