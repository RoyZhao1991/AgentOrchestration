"""Retention deletion workflow helpers."""

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple


PRIMARY_ARTIFACT = "primary_artifact"
DERIVED_EMBEDDING = "derived_embedding"
DERIVED_INDEX = "derived_index"


@dataclass(frozen=True)
class StoredRecord:
    """A primary artifact or derived data record held by a store."""

    record_id: str
    payload: Any
    source_id: Optional[str] = None


@dataclass(frozen=True)
class DeletionStoreRef:
    """Store metadata captured in a deletion manifest."""

    store: str
    data_class: str
    role: str


@dataclass(frozen=True)
class DeletionManifest:
    """A durable description of all stores covered by a deletion job."""

    workspace_id: str
    artifact_id: str
    stores: Tuple[DeletionStoreRef, ...]
    created_at: datetime

    @property
    def store_names(self) -> Tuple[str, ...]:
        return tuple(store.store for store in self.stores)


@dataclass(frozen=True)
class DeletionCompletion:
    """Completion evidence for one data store."""

    store: str
    data_class: str
    record_ids: Tuple[str, ...]
    completed_at: datetime

    @property
    def record_count(self) -> int:
        return len(self.record_ids)


@dataclass(frozen=True)
class DeletionResult:
    """Result returned after a cascade deletion job."""

    manifest: DeletionManifest
    completions: Tuple[DeletionCompletion, ...]

    @property
    def manifest_completed(self) -> bool:
        completion_stores = tuple(
            completion.store for completion in self.completions
        )
        return completion_stores == self.manifest.store_names

    def completion_for(self, store: str) -> DeletionCompletion:
        for completion in self.completions:
            if completion.store == store:
                return completion
        raise KeyError(store)


@dataclass(frozen=True)
class ReconciliationResult:
    """Result returned after stale derived data reconciliation."""

    completions: Tuple[DeletionCompletion, ...]

    @property
    def removed_record_ids(self) -> Tuple[str, ...]:
        removed = []
        for completion in self.completions:
            removed.extend(completion.record_ids)
        return tuple(removed)


class InMemoryRetentionStore:
    """Small retention store implementation for workflow tests and demos."""

    def __init__(self, name: str, data_class: str):
        self.name = name
        self.data_class = data_class
        self._records: Dict[str, StoredRecord] = {}
        self._lock = RLock()

    def put(
        self,
        record_id: str,
        payload: Any,
        source_id: Optional[str] = None,
    ) -> StoredRecord:
        with self._lock:
            record = StoredRecord(
                record_id=record_id,
                payload=payload,
                source_id=source_id,
            )
            self._records[record_id] = record
            return record

    def get(self, record_id: str) -> Optional[StoredRecord]:
        with self._lock:
            return self._records.get(record_id)

    def has(self, record_id: str) -> bool:
        return self.get(record_id) is not None

    def all_records(self) -> Tuple[StoredRecord, ...]:
        with self._lock:
            return tuple(self._records.values())

    def records_for_source(self, source_id: str) -> Tuple[StoredRecord, ...]:
        with self._lock:
            return tuple(
                record
                for record in self._records.values()
                if record.source_id == source_id
            )

    def delete(self, record_id: str) -> bool:
        with self._lock:
            return self._records.pop(record_id, None) is not None

    def delete_for_source(self, source_id: str) -> Tuple[str, ...]:
        with self._lock:
            record_ids = tuple(
                record.record_id
                for record in self._records.values()
                if record.source_id == source_id
            )
            for record_id in record_ids:
                self._records.pop(record_id, None)
            return record_ids


class RetentionDeletionWorkflow:
    """Coordinates source artifact deletion and derived data cleanup."""

    def cascade_delete(
        self,
        workspace_id: str,
        artifact_id: str,
        primary_stores: Sequence[InMemoryRetentionStore],
        derived_stores: Sequence[InMemoryRetentionStore],
        now: Optional[datetime] = None,
    ) -> DeletionResult:
        completed_at = now or _utc_now()
        stores = self._manifest_stores(primary_stores, derived_stores)
        manifest = DeletionManifest(
            workspace_id=workspace_id,
            artifact_id=artifact_id,
            stores=stores,
            created_at=completed_at,
        )
        completions = []

        for store in primary_stores:
            deleted = store.delete(artifact_id)
            record_ids = (artifact_id,) if deleted else ()
            completions.append(
                DeletionCompletion(
                    store=store.name,
                    data_class=store.data_class,
                    record_ids=record_ids,
                    completed_at=completed_at,
                )
            )

        for store in derived_stores:
            record_ids = store.delete_for_source(artifact_id)
            completions.append(
                DeletionCompletion(
                    store=store.name,
                    data_class=store.data_class,
                    record_ids=record_ids,
                    completed_at=completed_at,
                )
            )

        return DeletionResult(
            manifest=manifest,
            completions=tuple(completions),
        )

    def reconcile_stale_derived(
        self,
        primary_stores: Sequence[InMemoryRetentionStore],
        derived_stores: Sequence[InMemoryRetentionStore],
        now: Optional[datetime] = None,
    ) -> ReconciliationResult:
        completed_at = now or _utc_now()
        source_ids = self._source_ids(primary_stores)
        completions = []

        for store in derived_stores:
            stale_ids = tuple(
                record.record_id
                for record in store.all_records()
                if record.source_id is not None
                and record.source_id not in source_ids
            )
            for record_id in stale_ids:
                store.delete(record_id)
            completions.append(
                DeletionCompletion(
                    store=store.name,
                    data_class=store.data_class,
                    record_ids=stale_ids,
                    completed_at=completed_at,
                )
            )

        return ReconciliationResult(completions=tuple(completions))

    def _manifest_stores(
        self,
        primary_stores: Sequence[InMemoryRetentionStore],
        derived_stores: Sequence[InMemoryRetentionStore],
    ) -> Tuple[DeletionStoreRef, ...]:
        refs = []
        refs.extend(self._store_refs(primary_stores, role="primary"))
        refs.extend(self._store_refs(derived_stores, role="derived"))
        return tuple(refs)

    def _store_refs(
        self,
        stores: Iterable[InMemoryRetentionStore],
        role: str,
    ) -> Tuple[DeletionStoreRef, ...]:
        return tuple(
            DeletionStoreRef(
                store=store.name,
                data_class=store.data_class,
                role=role,
            )
            for store in stores
        )

    def _source_ids(
        self,
        primary_stores: Sequence[InMemoryRetentionStore],
    ) -> Tuple[str, ...]:
        source_ids = set()
        for store in primary_stores:
            for record in store.all_records():
                source_ids.add(record.record_id)
        return tuple(source_ids)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
