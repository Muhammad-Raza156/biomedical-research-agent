# ---------- Stage 1: build wheels ----------
FROM python:3.13.17-slim-bookworm AS builder
WORKDIR /build
COPY orchestration-service/requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.13.17-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src:/app
RUN useradd --create-home --uid 10001 app
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels
WORKDIR /app
COPY shared ./shared
COPY orchestration-service/src ./src
RUN mkdir -p /data/staging && chown -R app:app /data/staging
USER app
CMD ["python", "-m", "main"]
