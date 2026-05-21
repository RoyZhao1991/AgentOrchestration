import os
import stat

import pytest

from src.agent.sandbox import AgentSandbox, SANDBOX_DIRECTORY_MODE


pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX permission bits are required for mode assertions",
)


def mode_for(path):
    return stat.S_IMODE(path.stat().st_mode)


def test_sandbox_directories_are_owner_only_under_permissive_umask(tmp_path):
    original_umask = os.umask(0)
    try:
        sandbox = AgentSandbox(str(tmp_path / "sandbox-root"))
        sandbox_path = sandbox.create("agent-1")
    finally:
        os.umask(original_umask)

    assert mode_for(sandbox.base_path) == SANDBOX_DIRECTORY_MODE
    assert mode_for(sandbox_path) == SANDBOX_DIRECTORY_MODE


def test_existing_sandbox_root_is_tightened(tmp_path):
    base_path = tmp_path / "existing-root"
    base_path.mkdir(mode=0o777)
    base_path.chmod(0o777)

    sandbox = AgentSandbox(str(base_path))

    assert mode_for(sandbox.base_path) == SANDBOX_DIRECTORY_MODE
