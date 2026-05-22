from unittest.mock import Mock

import pytest

from src.sdk.client import OrchestratorClient


@pytest.mark.parametrize("blank_name", ["", "   ", "\t\n"])
def test_register_agent_rejects_blank_name_before_post(blank_name):
    client = OrchestratorClient(
        base_url="https://example.test",
        api_key="test-key",
    )
    client._request = Mock()

    with pytest.raises(
        ValueError,
        match="Agent name must be a non-empty string",
    ):
        client.register_agent(blank_name, "worker.processor")

    client._request.assert_not_called()


def test_register_agent_trims_name_before_post():
    client = OrchestratorClient(
        base_url="https://example.test",
        api_key="test-key",
    )
    client._request = Mock(return_value={"id": "agent-1"})

    result = client.register_agent(
        "  runner  ",
        "worker.processor",
        config={"queue": "default"},
    )

    assert result == {"id": "agent-1"}
    client._request.assert_called_once_with(
        "POST",
        "/agents",
        {
            "name": "runner",
            "agent_type": "worker.processor",
            "config": {"queue": "default"},
        },
    )
