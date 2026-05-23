from src.common.deletion import (
    DERIVED_EMBEDDING,
    DERIVED_INDEX,
    PRIMARY_ARTIFACT,
    InMemoryRetentionStore,
    RetentionDeletionWorkflow,
)


def test_cascade_deletes_primary_and_derived_records():
    artifacts = InMemoryRetentionStore("artifact-store", PRIMARY_ARTIFACT)
    embeddings = InMemoryRetentionStore("embedding-store", DERIVED_EMBEDDING)
    index = InMemoryRetentionStore("search-index", DERIVED_INDEX)
    artifacts.put("artifact-1", {"body": "private task output"})
    embeddings.put("embedding-1", [0.1, 0.2], source_id="artifact-1")
    embeddings.put("embedding-2", [0.3, 0.4], source_id="artifact-1")
    embeddings.put("embedding-3", [0.5, 0.6], source_id="artifact-2")
    index.put("index-1", {"chunk": 1}, source_id="artifact-1")

    result = RetentionDeletionWorkflow().cascade_delete(
        workspace_id="workspace-1",
        artifact_id="artifact-1",
        primary_stores=[artifacts],
        derived_stores=[embeddings, index],
    )

    assert not artifacts.has("artifact-1")
    assert not embeddings.has("embedding-1")
    assert not embeddings.has("embedding-2")
    assert not index.has("index-1")
    assert embeddings.has("embedding-3")
    assert result.completion_for("artifact-store").record_ids == (
        "artifact-1",
    )
    assert result.completion_for("embedding-store").record_ids == (
        "embedding-1",
        "embedding-2",
    )


def test_manifest_and_completions_list_every_affected_store():
    artifacts = InMemoryRetentionStore("artifact-store", PRIMARY_ARTIFACT)
    cold = InMemoryRetentionStore("cold-artifact-store", PRIMARY_ARTIFACT)
    embeddings = InMemoryRetentionStore("embedding-store", DERIVED_EMBEDDING)
    index = InMemoryRetentionStore("search-index", DERIVED_INDEX)
    artifacts.put("artifact-1", {"body": "delete me"})
    embeddings.put("embedding-1", [0.1], source_id="artifact-1")

    result = RetentionDeletionWorkflow().cascade_delete(
        workspace_id="workspace-1",
        artifact_id="artifact-1",
        primary_stores=[artifacts, cold],
        derived_stores=[embeddings, index],
    )

    assert result.manifest.workspace_id == "workspace-1"
    assert result.manifest.artifact_id == "artifact-1"
    assert result.manifest.store_names == (
        "artifact-store",
        "cold-artifact-store",
        "embedding-store",
        "search-index",
    )
    assert [completion.store for completion in result.completions] == list(
        result.manifest.store_names
    )
    assert result.manifest_completed
    assert result.completion_for("cold-artifact-store").record_ids == ()
    assert result.completion_for("search-index").record_ids == ()


def test_reconciliation_removes_stale_derived_records():
    artifacts = InMemoryRetentionStore("artifact-store", PRIMARY_ARTIFACT)
    embeddings = InMemoryRetentionStore("embedding-store", DERIVED_EMBEDDING)
    artifacts.put("artifact-live", {"body": "keep"})
    embeddings.put("embedding-live", [0.1], source_id="artifact-live")
    embeddings.put("embedding-stale", [0.9], source_id="artifact-deleted")

    result = RetentionDeletionWorkflow().reconcile_stale_derived(
        primary_stores=[artifacts],
        derived_stores=[embeddings],
    )

    assert embeddings.has("embedding-live")
    assert not embeddings.has("embedding-stale")
    assert result.removed_record_ids == ("embedding-stale",)
    assert result.completions[0].store == "embedding-store"


def test_reconciliation_checks_all_primary_stores():
    hot = InMemoryRetentionStore("hot-artifact-store", PRIMARY_ARTIFACT)
    cold = InMemoryRetentionStore("cold-artifact-store", PRIMARY_ARTIFACT)
    embeddings = InMemoryRetentionStore("embedding-store", DERIVED_EMBEDDING)
    cold.put("artifact-archived", {"body": "still retained"})
    embeddings.put("embedding-archived", [0.8], source_id="artifact-archived")
    embeddings.put("embedding-orphan", [0.9], source_id="missing-artifact")

    result = RetentionDeletionWorkflow().reconcile_stale_derived(
        primary_stores=[hot, cold],
        derived_stores=[embeddings],
    )

    assert embeddings.has("embedding-archived")
    assert not embeddings.has("embedding-orphan")
    assert result.completions[0].record_ids == ("embedding-orphan",)


def test_completion_helpers_expose_store_audit_details():
    artifacts = InMemoryRetentionStore("artifact-store", PRIMARY_ARTIFACT)
    embeddings = InMemoryRetentionStore("embedding-store", DERIVED_EMBEDDING)
    artifacts.put("artifact-1", {"body": "delete"})
    embeddings.put("embedding-1", [0.1], source_id="artifact-1")
    embeddings.put("embedding-2", [0.2], source_id="artifact-1")

    result = RetentionDeletionWorkflow().cascade_delete(
        workspace_id="workspace-1",
        artifact_id="artifact-1",
        primary_stores=[artifacts],
        derived_stores=[embeddings],
    )

    embedding_completion = result.completion_for("embedding-store")
    assert embedding_completion.record_count == 2
    assert result.manifest_completed


def test_completion_lookup_fails_for_unknown_store():
    result = RetentionDeletionWorkflow().cascade_delete(
        workspace_id="workspace-1",
        artifact_id="artifact-1",
        primary_stores=[],
        derived_stores=[],
    )

    try:
        result.completion_for("missing-store")
    except KeyError as exc:
        assert exc.args == ("missing-store",)
    else:
        raise AssertionError("unknown completion lookup should fail")


def test_records_for_source_reports_pending_derived_data():
    embeddings = InMemoryRetentionStore("embedding-store", DERIVED_EMBEDDING)
    embeddings.put("embedding-1", [0.1], source_id="artifact-1")
    embeddings.put("embedding-2", [0.2], source_id="artifact-2")

    records = embeddings.records_for_source("artifact-1")

    assert [record.record_id for record in records] == ["embedding-1"]
