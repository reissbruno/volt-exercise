FROM python:3.11-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy source
COPY server.py ./
COPY data/exercises.json ./data/
COPY scripts/seed.py ./scripts/

# Seed database during build — bakes the populated DB into the image
RUN DB_PATH=data/exercises.db \
    EXERCISES_JSON_PATH=data/exercises.json \
    uv run python scripts/seed.py

# Copy remaining files
COPY frontend/ ./frontend/
COPY public/ ./public/

# Runtime environment
ENV DB_PATH=data/exercises.db \
    JWT_SECRET=change-this-secret \
    ALLOWED_ORIGINS=* \
    PORT=8000

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
