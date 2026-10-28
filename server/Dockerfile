# Memvine server: API plus the in-process synthesis worker.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    # fastembed downloads its model here on first use; mount a volume to keep it.
    FASTEMBED_CACHE_PATH=/var/cache/fastembed

WORKDIR /app
COPY pyproject.toml README.md ./
COPY memvine_server ./memvine_server
RUN pip install --no-cache-dir . && mkdir -p /var/cache/fastembed

EXPOSE 8400
CMD ["memvine-server", "serve", "--host", "0.0.0.0", "--port", "8400"]
