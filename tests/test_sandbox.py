import resource

import pytest

from src.agent.sandbox import AgentSandbox, ResourceLimits


def test_apply_limits_enforces_disk_mb_with_file_size_limit(monkeypatch):
    calls = []

    def fake_setrlimit(limit, values):
        calls.append((limit, values))

    monkeypatch.setattr(resource, "setrlimit", fake_setrlimit)

    sandbox = AgentSandbox()
    limits = ResourceLimits(cpu_time=2, memory_mb=3, disk_mb=4)

    sandbox.apply_limits("agent-1", limits)

    assert (resource.RLIMIT_CPU, (2, 2)) in calls
    assert (resource.RLIMIT_AS, (3 * 1024 * 1024, 3 * 1024 * 1024)) in calls
    assert (resource.RLIMIT_FSIZE, (4 * 1024 * 1024, 4 * 1024 * 1024)) in calls


def test_apply_limits_fails_if_disk_limit_is_unsupported(monkeypatch):
    monkeypatch.delattr(resource, "RLIMIT_FSIZE", raising=False)

    sandbox = AgentSandbox()

    with pytest.raises(RuntimeError, match="disk_mb requires"):
        sandbox.apply_limits("agent-1", ResourceLimits(disk_mb=4))


def test_create_applies_provided_resource_limits(monkeypatch, tmp_path):
    applied = []
    sandbox = AgentSandbox(base_path=str(tmp_path))
    limits = ResourceLimits(disk_mb=None)

    def fake_apply_limits(agent_id, received_limits):
        applied.append((agent_id, received_limits))

    monkeypatch.setattr(sandbox, "apply_limits", fake_apply_limits)

    path = sandbox.create("agent-1", limits)

    assert path == tmp_path / "agent-1"
    assert applied == [("agent-1", limits)]
