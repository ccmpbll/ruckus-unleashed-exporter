FROM python:3.12-slim

LABEL org.opencontainers.image.title="ruckus-unleashed-exporter"
LABEL org.opencontainers.image.description="Prometheus exporter and Loki event pusher for Ruckus Unleashed access points"
LABEL org.opencontainers.image.source="https://github.com/ccampbell/ruckus-unleashed-exporter"
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

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ruckus_exporter.py .

EXPOSE 9785

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9785/health')" || exit 1

ENTRYPOINT ["python", "-u", "ruckus_exporter.py"]
