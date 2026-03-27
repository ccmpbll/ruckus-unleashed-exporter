FROM python:3.12-slim

LABEL org.opencontainers.image.title="ruckus-unleashed-exporter"
LABEL org.opencontainers.image.description="Prometheus exporter and Loki event pusher for Ruckus Unleashed access points"
LABEL org.opencontainers.image.source="https://github.com/ccmpbll/ruckus-unleashed-exporter"
LABEL org.opencontainers.image.licenses="MIT"

# --- Required: no defaults, container will exit with an error if not set ---
ENV RUCKUS_HOST=""
ENV RUCKUS_PASSWORD=""

# --- Auth ---
ENV RUCKUS_USER=""

# --- Exporter ---
ENV EXPORTER_PORT="9785"

# --- Loki (leave LOKI_URL empty to disable) ---
ENV LOKI_URL=""
ENV LOKI_JOB="ruckus_unleashed"

# --- Logging: DEBUG, INFO, WARNING, ERROR ---
ENV LOG_LEVEL="INFO"

# --- Debug: set to 1 to dump raw API responses on first scrape ---
ENV DEBUG_DUMP="0"

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ruckus_exporter.py .

EXPOSE 9785


ENTRYPOINT ["python", "-u", "ruckus_exporter.py"]
