FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps (optional: ca-certificates is already included, but keep updated)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# Non-root (optional)
RUN useradd -ms /bin/bash appuser
USER appuser

EXPOSE 8000
# Use the Cloud Run $PORT if present (defaults to 8000 for local/dev)
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]

# # Use env ALLOWED_ORIGINS for CORS, API_KEY, etc.
# CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
