# Container image for the sci-rag server (Cloud Run friendly).
#
#   docker build -t my-scientific-kb .
#   docker run -p 8080:8080 --env-file .env my-scientific-kb
#
# The image deliberately does NOT include the docling extra; PDF-heavy
# ingestion is better run where you can afford the larger image, or with the
# pypdf fallback.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.14-slim-bookworm
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY src ./src
COPY domain ./domain
COPY alembic.ini ./
COPY migrations ./migrations
ENV PYTHONUNBUFFERED=1 \
    SCI_RAG_SERVER_HOST=0.0.0.0
EXPOSE 8080
# Cloud Run injects PORT; sci-rag serve honors it.
CMD ["sci-rag", "serve"]
