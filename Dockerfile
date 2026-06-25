# Console container. CPU-only; the GPU re-embed batch runs separately on Nova.
FROM python:3.12-slim

WORKDIR /app

# Install deps first for layer caching.
COPY pyproject.toml ./
RUN pip install --no-cache-dir \
        "fastapi>=0.115" "uvicorn[standard]>=0.30" "jinja2>=3.1" \
        "python-multipart>=0.0.9" "numpy>=2.0" "duckdb>=1.1"

COPY bench/ ./bench/
COPY schema/ ./schema/
COPY scripts/ ./scripts/

EXPOSE 8800
CMD ["python", "-m", "bench"]
