FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/api/pyproject.toml ./pyproject.toml
COPY services/api/src ./src
RUN pip install .

COPY services/api/alembic.ini ./alembic.ini
COPY services/api/alembic ./alembic

CMD ["celery", "-A", "knowledge_core.workers.celery_app", "worker", "-Q", "ingestion,maintenance", "--loglevel=info"]

