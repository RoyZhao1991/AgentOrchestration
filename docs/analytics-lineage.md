# Analytics Lineage

Transformed analytics datasets must publish with machine-readable lineage. The
lineage block lets an analyst trace every reported metric back to the raw table
and transformation version that produced it.

## Required Metadata

Each transformed dataset includes:

- `source_table`: the raw table used by the transform, for example
  `raw_task_records`
- `transform_version`: a stable transform name and version, for example
  `task-completion/v3`
- `generated_at`: the UTC timestamp for the transform run
- `source_columns`: the raw columns used to derive the published metric

## Publishing Rule

Use `TransformedDataset` or `build_transformed_dataset` from
`src.common.lineage` when preparing an analytics output. `AnalyticsPublisher`
rejects publish attempts that do not include lineage metadata, so incomplete
datasets cannot silently enter reporting tables.

```python
from src.common.lineage import AnalyticsPublisher, build_transformed_dataset

dataset = build_transformed_dataset(
    name="task_completion_daily",
    records=[{"day": "2026-05-23", "completed": 12}],
    source_table="raw_task_records",
    transform_version="task-completion/v3",
    source_columns=("task_id", "status", "completed_at"),
)

AnalyticsPublisher().publish(dataset)
```

## Tracing A Metric

To trace a metric, inspect the dataset lineage:

1. Find the reporting dataset that contains the metric.
2. Read `lineage.source_table` to identify the raw table.
3. Read `lineage.transform_version` to identify the transform implementation.
4. Use `lineage.source_columns` to identify which raw fields contributed to
   the metric.

`trace_metric_to_source(metric_name, dataset)` returns this trace as a
dictionary that can be attached to analyst tooling, audit exports, or support
responses.
