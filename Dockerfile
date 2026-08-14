FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ARG AIOA_SOURCE_SHA

LABEL org.opencontainers.image.title="Memory Patch for AIOA demo runtime" \
      org.opencontainers.image.source="https://github.com/luciferprosun/Memory-Patch-for-AIOA-Hackathon-CockroachDB" \
      org.opencontainers.image.revision="${AIOA_SOURCE_SHA}"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

COPY requirements-runtime.txt requirements-ui.txt ./
RUN python -m pip install --no-cache-dir --requirement requirements-runtime.txt

COPY AGENTS.md ./
COPY src/ ./src/
COPY config/ ./config/
COPY schemas/ ./schemas/
COPY sql/cockroachdb/migrations/ ./sql/cockroachdb/migrations/
COPY scripts/run_demo_runtime_1a.py scripts/run_cockroachdb_migrations.py ./scripts/
COPY tests/fixtures/step38_german_law_cases.json ./tests/fixtures/step38_german_law_cases.json

RUN groupadd --gid 10001 aioa \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin aioa \
    && chown -R root:root /app \
    && chmod -R a-w /app \
    && python -m compileall -q /app/src /app/scripts \
    && find /app -type d -name __pycache__ -prune -exec rm -r {} +

USER 10001:10001

EXPOSE 8000

CMD ["python", "scripts/run_demo_runtime_1a.py", "serve"]
