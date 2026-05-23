import multiprocessing

from src.common.event_log import JSONLEventLogWriter


def _append_events(path, worker_id, event_count):
    writer = JSONLEventLogWriter(path)
    for index in range(event_count):
        writer.append(
            {
                "event": "task.completed",
                "worker": worker_id,
                "sequence": index,
            }
        )


def test_concurrent_appenders_produce_valid_jsonl(tmp_path):
    log_path = tmp_path / "events.jsonl"
    process_count = 4
    event_count = 30
    context = multiprocessing.get_context(
        "fork" if "fork" in multiprocessing.get_all_start_methods()
        else "spawn"
    )
    processes = [
        context.Process(
            target=_append_events,
            args=(str(log_path), worker_id, event_count),
        )
        for worker_id in range(process_count)
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0

    writer = JSONLEventLogWriter(log_path)
    events = writer.read_events()

    assert writer.malformed_record_count() == 0
    assert len(events) == process_count * event_count
    assert {
        (event["worker"], event["sequence"])
        for event in events
    } == {
        (worker_id, index)
        for worker_id in range(process_count)
        for index in range(event_count)
    }


def test_rotation_preserves_complete_events(tmp_path):
    log_path = tmp_path / "events.jsonl"
    writer = JSONLEventLogWriter(log_path, max_bytes=180)

    for index in range(40):
        writer.append(
            {
                "event": "task.updated",
                "sequence": index,
                "payload": "x" * 40,
            }
        )

    paths = writer.log_paths()
    events = writer.read_events()

    assert len(paths) > 1
    assert writer.malformed_record_count() == 0
    assert sorted(event["sequence"] for event in events) == list(range(40))
    for path in paths:
        with path.open("rb") as handle:
            assert all(line.endswith(b"\n") for line in handle)
