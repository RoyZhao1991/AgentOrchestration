import pytest

from src.common.lineage import (
    AnalyticsPublisher,
    DataLineageMetadata,
    LineageValidationError,
    TransformedDataset,
    build_transformed_dataset,
    trace_metric_to_source,
)


def test_publish_includes_machine_readable_lineage_metadata():
    dataset = build_transformed_dataset(
        name="task_completion_daily",
        records=[{"day": "2026-05-23", "completed": 12}],
        source_table="raw_task_records",
        transform_version="task-completion/v3",
        generated_at="2026-05-23T04:25:00Z",
        source_columns=("task_id", "status", "completed_at"),
    )

    payload = AnalyticsPublisher().publish(dataset)

    assert payload["lineage"] == {
        "source_table": "raw_task_records",
        "transform_version": "task-completion/v3",
        "generated_at": "2026-05-23T04:25:00Z",
        "source_columns": ["task_id", "status", "completed_at"],
    }


def test_publish_fails_when_lineage_metadata_is_missing():
    dataset = TransformedDataset(
        name="task_completion_daily",
        records=[{"day": "2026-05-23", "completed": 12}],
        lineage=None,
    )
    publisher = AnalyticsPublisher()

    with pytest.raises(
        LineageValidationError,
        match="missing lineage metadata",
    ):
        publisher.publish(dataset)

    assert publisher.get("task_completion_daily") is None


def test_lineage_requires_source_table_and_transform_version():
    with pytest.raises(LineageValidationError, match="source_table"):
        DataLineageMetadata(
            source_table=" ",
            transform_version="task-completion/v3",
        )

    with pytest.raises(LineageValidationError, match="transform_version"):
        DataLineageMetadata(
            source_table="raw_task_records",
            transform_version="",
        )


def test_metric_trace_points_to_source_data_and_transform_version():
    dataset = build_transformed_dataset(
        name="task_completion_daily",
        records=[],
        source_table="raw_task_records",
        transform_version="task-completion/v3",
        generated_at="2026-05-23T04:25:00Z",
        source_columns=("task_id", "status"),
    )

    trace = trace_metric_to_source("completed_tasks", dataset)

    assert trace == {
        "metric": "completed_tasks",
        "dataset": "task_completion_daily",
        "source_table": "raw_task_records",
        "source_columns": ["task_id", "status"],
        "transform_version": "task-completion/v3",
        "generated_at": "2026-05-23T04:25:00Z",
    }
