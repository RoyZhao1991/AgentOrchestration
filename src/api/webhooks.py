"""Webhook registration and delivery routes."""

import copy
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, HTTPException


router = APIRouter(prefix="/webhooks", tags=["webhooks"])


class WebhookStore:
    def __init__(self):
        self._subscriptions: Dict[str, Dict[str, Any]] = {}
        self._subscription_keys: Dict[Tuple[str, str], str] = {}
        self._deliveries: Dict[str, Dict[str, Any]] = {}
        self._delivery_keys: Dict[Tuple[str, str], str] = {}
        self._retry_keys: Dict[str, Dict[str, Any]] = {}

    def clear(self) -> None:
        self._subscriptions.clear()
        self._subscription_keys.clear()
        self._deliveries.clear()
        self._delivery_keys.clear()
        self._retry_keys.clear()

    def create_subscription(
        self,
        workspace_id: str,
        url: str,
        event_types: List[str],
        enabled: bool = True,
    ) -> Dict[str, Any]:
        normalized_url = normalize_webhook_url(url)
        event_type_set = sorted({
            event_type.strip()
            for event_type in event_types
        })
        if not workspace_id.strip():
            raise ValueError("workspace_id is required")
        if not event_type_set or any(
            not event_type for event_type in event_type_set
        ):
            raise ValueError("event_types must not be empty")

        duplicate_key = (workspace_id, normalized_url)
        existing_id = self._subscription_keys.get(duplicate_key)
        if existing_id:
            return self._public_subscription(self._subscriptions[existing_id])

        subscription_id = str(uuid.uuid4())
        now = time.time()
        self._subscriptions[subscription_id] = {
            "id": subscription_id,
            "workspace_id": workspace_id,
            "url": url,
            "normalized_url": normalized_url,
            "event_types": event_type_set,
            "enabled": enabled,
            "version": 1,
            "callback_secret": str(uuid.uuid4()),
            "created_at": now,
            "updated_at": now,
        }
        self._subscription_keys[duplicate_key] = subscription_id
        return self._public_subscription(self._subscriptions[subscription_id])

    def update_subscription(
        self,
        subscription_id: str,
        workspace_id: str,
        url: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        subscription = self._subscriptions.get(subscription_id)
        if not subscription or subscription["workspace_id"] != workspace_id:
            raise KeyError(subscription_id)

        if url is not None:
            normalized_url = normalize_webhook_url(url)
            duplicate_key = (workspace_id, normalized_url)
            duplicate_id = self._subscription_keys.get(duplicate_key)
            if duplicate_id and duplicate_id != subscription_id:
                raise ValueError("webhook URL already registered")
            old_key = (workspace_id, subscription["normalized_url"])
            self._subscription_keys.pop(old_key, None)
            self._subscription_keys[duplicate_key] = subscription_id
            subscription["url"] = url
            subscription["normalized_url"] = normalized_url
            subscription["version"] += 1

        if enabled is not None:
            subscription["enabled"] = enabled
        subscription["updated_at"] = time.time()
        return self._public_subscription(subscription)

    def list_subscriptions(self, workspace_id: str) -> List[Dict[str, Any]]:
        return [
            self._public_subscription(subscription)
            for subscription in self._subscriptions.values()
            if subscription["workspace_id"] == workspace_id
        ]

    def create_delivery(
        self,
        workspace_id: str,
        event_type: str,
        payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        if idempotency_key:
            delivery_id = self._delivery_keys.get((
                workspace_id,
                idempotency_key,
            ))
            if delivery_id:
                return self._public_delivery(self._deliveries[delivery_id])

        subscription = self._subscription_for_event(workspace_id, event_type)
        if subscription is None:
            raise LookupError("no enabled subscription for event")

        delivery_id = str(uuid.uuid4())
        now = time.time()
        delivery = {
            "id": delivery_id,
            "workspace_id": workspace_id,
            "subscription_id": subscription["id"],
            "subscription_version": subscription["version"],
            "event_type": event_type,
            "payload": copy.deepcopy(payload),
            "status": "delivered",
            "attempt": 1,
            "callback_url": subscription["normalized_url"],
            "created_at": now,
            "updated_at": now,
        }
        self._deliveries[delivery_id] = delivery
        if idempotency_key:
            self._delivery_keys[(workspace_id, idempotency_key)] = delivery_id
        return self._public_delivery(delivery)

    def retry_delivery(
        self,
        delivery_id: str,
        workspace_id: str,
    ) -> Dict[str, Any]:
        delivery = self._deliveries.get(delivery_id)
        if not delivery or delivery["workspace_id"] != workspace_id:
            raise KeyError(delivery_id)
        if delivery_id in self._retry_keys:
            return copy.deepcopy(self._retry_keys[delivery_id])

        subscription = self._subscriptions.get(delivery["subscription_id"])
        if subscription is None or not subscription["enabled"]:
            raise PermissionError("subscription is disabled")
        if subscription["version"] != delivery["subscription_version"]:
            raise PermissionError("subscription endpoint has rotated")

        retry = self._public_delivery(delivery)
        retry["retry_id"] = str(uuid.uuid4())
        retry["status"] = "retry_scheduled"
        retry["attempt"] = delivery["attempt"] + 1
        retry["retried_at"] = time.time()
        self._retry_keys[delivery_id] = copy.deepcopy(retry)
        return retry

    def _subscription_for_event(
        self,
        workspace_id: str,
        event_type: str,
    ) -> Optional[Dict[str, Any]]:
        for subscription in self._subscriptions.values():
            if (
                subscription["workspace_id"] == workspace_id
                and subscription["enabled"]
                and event_type in subscription["event_types"]
            ):
                return subscription
        return None

    def _public_subscription(
        self,
        subscription: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "id": subscription["id"],
            "workspace_id": subscription["workspace_id"],
            "url": subscription["url"],
            "event_types": list(subscription["event_types"]),
            "enabled": subscription["enabled"],
            "version": subscription["version"],
            "created_at": subscription["created_at"],
            "updated_at": subscription["updated_at"],
        }

    def _public_delivery(self, delivery: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": delivery["id"],
            "workspace_id": delivery["workspace_id"],
            "subscription_id": delivery["subscription_id"],
            "event_type": delivery["event_type"],
            "status": delivery["status"],
            "attempt": delivery["attempt"],
            "created_at": delivery["created_at"],
            "updated_at": delivery["updated_at"],
        }


def normalize_webhook_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    if scheme not in {"http", "https"} or not hostname:
        raise ValueError("webhook URL must be absolute HTTP(S)")

    port = parsed.port
    netloc = hostname
    if port and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"

    path = quote(parsed.path or "/", safe="/%")
    if len(path) > 1:
        path = path.rstrip("/")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


webhook_store = WebhookStore()


@router.post("/subscriptions")
async def create_subscription(body: Dict[str, Any]):
    try:
        return webhook_store.create_subscription(
            workspace_id=body.get("workspace_id", ""),
            url=body.get("url", ""),
            event_types=body.get("event_types", []),
            enabled=body.get("enabled", True),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.patch("/subscriptions/{subscription_id}")
async def update_subscription(subscription_id: str, body: Dict[str, Any]):
    try:
        return webhook_store.update_subscription(
            subscription_id=subscription_id,
            workspace_id=body.get("workspace_id", ""),
            url=body.get("url"),
            enabled=body.get("enabled"),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="subscription not found",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/subscriptions")
async def list_subscriptions(workspace_id: str):
    return {"subscriptions": webhook_store.list_subscriptions(workspace_id)}


@router.post("/deliveries")
async def create_delivery(body: Dict[str, Any]):
    try:
        return webhook_store.create_delivery(
            workspace_id=body.get("workspace_id", ""),
            event_type=body.get("event_type", ""),
            payload=body.get("payload", {}),
            idempotency_key=body.get("idempotency_key"),
        )
    except LookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/deliveries/{delivery_id}/retry")
async def retry_delivery(delivery_id: str, body: Dict[str, Any]):
    try:
        return webhook_store.retry_delivery(
            delivery_id=delivery_id,
            workspace_id=body.get("workspace_id", ""),
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="delivery not found",
        ) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
