from scripts.validate_docker_network_policy import validate_dockerfile_text


def test_runtime_stage_allows_local_no_index_install():
    dockerfile = """
    FROM python:3.11-slim AS dependencies
    RUN python -m pip wheel --wheel-dir /wheels .

    FROM python:3.11-slim AS runtime
    COPY --from=dependencies /wheels /wheels
    RUN --network=none python -m pip install --no-index ao
    """

    assert validate_dockerfile_text(dockerfile) == []


def test_runtime_stage_rejects_network_fetch_commands():
    dockerfile = """
    FROM python:3.11-slim AS dependencies
    RUN python -m pip wheel --wheel-dir /wheels .

    FROM python:3.11-slim AS runtime
    COPY --from=dependencies /wheels /wheels
    RUN --network=none python -m pip install agent-orchestrator
    """

    errors = validate_dockerfile_text(dockerfile)

    assert errors == [
        "network fetch in runtime stage: "
        "RUN --network=none python -m pip install agent-orchestrator"
    ]


def test_runtime_stage_requires_network_disabled_check():
    dockerfile = """
    FROM python:3.11-slim AS dependencies
    RUN python -m pip wheel --wheel-dir /wheels .

    FROM python:3.11-slim AS runtime
    COPY --from=dependencies /wheels /wheels
    RUN python -m pip install --no-index ao
    """

    assert validate_dockerfile_text(dockerfile) == [
        "runtime stage must include a RUN --network=none check"
    ]
