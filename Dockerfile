# syntax=docker/dockerfile:1.7

FROM python:3.11-slim AS dependencies

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip wheel --wheel-dir /wheels .


FROM python:3.11-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=dependencies /wheels /wheels

RUN --network=none \
    python -m pip install --no-index --find-links=/wheels agent-orchestrator \
    && rm -rf /wheels

RUN --network=none \
    python -c "import src.sdk.client; print('runtime import check passed')"

EXPOSE 8000

CMD ["uvicorn", "src.api.server:create_app", "--host", "0.0.0.0", "--port", "8000"]
