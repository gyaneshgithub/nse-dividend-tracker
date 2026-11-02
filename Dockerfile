FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    ANONYMIZED_TELEMETRY=False \
    HOST=0.0.0.0 \
    PORT=8901 \
    DIVIDEND_DB_DIR=/app/chroma_db \
    DIVIDEND_DATA_DIR=/app/data

WORKDIR /app

# onnxruntime (a ChromaDB dependency) needs the OpenMP runtime.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY server.py db.py market.py run.py ./
COPY templates ./templates

RUN mkdir -p /app/data /app/chroma_db

EXPOSE 8901

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8901/api/health', timeout=3)"

# Intended for local use behind Docker's port mapping. There is no auth layer.
CMD ["python", "run.py", "--no-venv"]
