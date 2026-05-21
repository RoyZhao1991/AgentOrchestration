import subprocess

from src.agent.runtime import AgentRuntime, RuntimeState


class CompletedProcess:
    def __init__(self, returncode):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class HangingProcess:
    def __init__(self):
        self.killed = False
        self.returncode = None

    def poll(self):
        return self.returncode

    def send_signal(self, _signal):
        return None

    def wait(self, timeout=None):
        if timeout is not None and not self.killed:
            raise subprocess.TimeoutExpired(cmd="agent", timeout=timeout)
        self.returncode = -9
        return self.returncode

    def kill(self):
        self.killed = True


def test_crashed_process_records_terminal_outcome_once_and_cleans_process():
    runtime = AgentRuntime()
    runtime._processes["agent-1"] = CompletedProcess(returncode=7)
    runtime._states["agent-1"] = RuntimeState.RUNNING

    assert runtime.get_state("agent-1") == RuntimeState.CRASHED
    outcome = runtime.get_terminal_outcome("agent-1")
    assert outcome.state == RuntimeState.CRASHED
    assert outcome.reason == "process exited with code 7"
    assert outcome.return_code == 7
    assert runtime.get_failure_reason("agent-1") == "process exited with code 7"
    assert "agent-1" not in runtime._processes

    assert runtime.get_state("agent-1") == RuntimeState.CRASHED
    assert runtime.get_terminal_outcome("agent-1") == outcome


def test_forced_stop_records_failure_reason_before_marking_stopped():
    runtime = AgentRuntime()
    runtime._processes["agent-1"] = HangingProcess()
    runtime._states["agent-1"] = RuntimeState.RUNNING

    assert runtime.stop("agent-1", timeout=1)

    outcome = runtime.get_terminal_outcome("agent-1")
    assert runtime.get_state("agent-1") == RuntimeState.STOPPED
    assert outcome.state == RuntimeState.STOPPED
    assert outcome.reason == "shutdown timed out after 1s; process killed"
    assert outcome.return_code == -9
    assert runtime.get_failure_reason("agent-1") == outcome.reason
    assert "agent-1" not in runtime._processes


def test_start_failure_records_auditable_terminal_reason(monkeypatch):
    def fail_to_start(*_args, **_kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(subprocess, "Popen", fail_to_start)
    runtime = AgentRuntime()

    assert not runtime.start("agent-1", ["agent"])

    outcome = runtime.get_terminal_outcome("agent-1")
    assert runtime.get_state("agent-1") == RuntimeState.CRASHED
    assert outcome.state == RuntimeState.CRASHED
    assert outcome.reason == "failed to start: permission denied"
    assert runtime.get_failure_reason("agent-1") == outcome.reason
