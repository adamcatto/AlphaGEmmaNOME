FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml /app/pyproject.toml
COPY config /app/config
COPY services/alphagenome_svc /app/services/alphagenome_svc

RUN uv sync --frozen --no-dev || uv sync --no-dev

EXPOSE 8001
CMD ["uv", "run", "uvicorn", "services.alphagenome_svc.app:app", "--host", "0.0.0.0", "--port", "8001"]
