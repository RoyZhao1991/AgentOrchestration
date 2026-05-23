"""Lineage metadata for transformed analytics datasets."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple


class LineageValidationError(ValueError):
    """Raised when a transformed dataset cannot be safely published."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _require_non_blank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LineageValidationError(f"{field_name} is required")
    return value.strip()


@dataclass(frozen=True)
class DataLineageMetadata:
    """Machine-readable lineage for an analytics transform output."""

    source_table: str
    transform_version: str
    generated_at: str = field(default_factory=_utc_now)
    source_columns: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "source_table",
            _require_non_blank(self.source_table, "source_table"),
        )
        object.__setattr__(
            self,
            "transform_version",
            _require_non_blank(self.transform_version, "transform_version"),
        )
        object.__setattr__(
            self,
            "generated_at",
            _require_non_blank(self.generated_at, "generated_at"),
        )
        columns = tuple(
            column.strip()
            for column in self.source_columns
            if column.strip()
        )
        object.__setattr__(self, "source_columns", columns)

    def to_dict(self) -> Dict[str, object]:
        return {
            "source_table": self.source_table,
            "transform_version": self.transform_version,
            "generated_at": self.generated_at,
            "source_columns": list(self.source_columns),
        }


@dataclass(frozen=True)
class TransformedDataset:
    """A publishable analytics dataset plus its transformation lineage."""

    name: str
    records: Sequence[Mapping[str, object]]
    lineage: Optional[DataLineageMetadata]

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _require_non_blank(self.name, "name"))

    def publish_payload(self) -> Dict[str, object]:
        lineage = validate_lineage(self)
        return {
            "name": self.name,
            "records": [dict(record) for record in self.records],
            "lineage": lineage.to_dict(),
        }


class AnalyticsPublisher:
    """Publishes transformed datasets only after lineage validation."""

    def __init__(self) -> None:
        self._published: Dict[str, Dict[str, object]] = {}

    def publish(self, dataset: TransformedDataset) -> Dict[str, object]:
        payload = dataset.publish_payload()
        self._published[dataset.name] = payload
        return payload

    def get(self, dataset_name: str) -> Optional[Dict[str, object]]:
        return self._published.get(dataset_name)


def build_transformed_dataset(
    name: str,
    records: Sequence[Mapping[str, object]],
    source_table: str,
    transform_version: str,
    generated_at: Optional[str] = None,
    source_columns: Iterable[str] = (),
) -> TransformedDataset:
    lineage = DataLineageMetadata(
        source_table=source_table,
        transform_version=transform_version,
        generated_at=generated_at or _utc_now(),
        source_columns=tuple(source_columns),
    )
    return TransformedDataset(name=name, records=records, lineage=lineage)


def validate_lineage(dataset: TransformedDataset) -> DataLineageMetadata:
    if dataset.lineage is None:
        raise LineageValidationError(
            f"dataset {dataset.name!r} is missing lineage metadata"
        )
    return dataset.lineage


def trace_metric_to_source(
    metric_name: str,
    dataset: TransformedDataset,
) -> Dict[str, object]:
    lineage = validate_lineage(dataset)
    return {
        "metric": _require_non_blank(metric_name, "metric_name"),
        "dataset": dataset.name,
        "source_table": lineage.source_table,
        "source_columns": list(lineage.source_columns),
        "transform_version": lineage.transform_version,
        "generated_at": lineage.generated_at,
    }
