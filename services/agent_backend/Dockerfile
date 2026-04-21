FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml /app/pyproject.toml
COPY config /app/config
COPY services/agent_backend /app/services/agent_backend

RUN uv sync --frozen --no-dev || uv sync --no-dev

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "services.agent_backend.app:app", "--host", "0.0.0.0", "--port", "8000"]
