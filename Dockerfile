FROM python:3.12-slim

# Install system dependencies including dictzip for StarDict .dict.dz compression
RUN apt-get update && apt-get install -y --no-install-recommends \
    dictzip \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first for caching
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy application source code
COPY src/ ./src/
RUN pip install --no-cache-dir -e .

# Create persistent data directories
RUN mkdir -p /app/data/raw /app/data/output

ENV ORDPRESSE_DATA_DIR=/app/data
ENV PORT=8088
ENV HOST=0.0.0.0

EXPOSE 8088

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8088/health || exit 1

CMD ["ordpresse", "serve", "--host", "0.0.0.0", "--port", "8088"]
