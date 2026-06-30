FROM python:3.11-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Install dependencies (cached layer)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code
COPY server.py ./
COPY frontend/ ./frontend/
COPY data/exercises.json ./data/

# public/ assets are large (~140 MB); mount as a volume in production.
# To include them in the image, uncomment:
# COPY public/ ./public/

# Runtime environment
ENV DB_PATH=data/exercises.db \
    JWT_SECRET=change-this-secret \
    ALLOWED_ORIGINS=* \
    PORT=8000

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
