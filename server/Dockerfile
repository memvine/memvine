# Memvine server: API plus the in-process synthesis worker.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    # fastembed downloads its model here on first use; mount a volume to keep it.
    FASTEMBED_CACHE_PATH=/var/cache/fastembed

WORKDIR /app
COPY pyproject.toml README.md requirements.lock ./
COPY memvine_server ./memvine_server
# The exact versions come from requirements.lock (see server/README.md to refresh it).
RUN pip install --no-cache-dir -r requirements.lock && pip install --no-cache-dir --no-deps . \
    && mkdir -p /var/cache/fastembed
# Bake the embedding model and conversation mode's reranker into the image, so a fresh container never
# downloads them on start.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')" \
    && python -c "from fastembed.rerank.cross_encoder import TextCrossEncoder; TextCrossEncoder('jinaai/jina-reranker-v1-turbo-en')"

EXPOSE 8400
# MEMVINE_HOST=:: listens on IPv6 and IPv4 together (Railway's private network is IPv6).
ENV MEMVINE_HOST=0.0.0.0
CMD ["sh", "-c", "exec memvine-server serve --host \"$MEMVINE_HOST\" --port 8400"]
