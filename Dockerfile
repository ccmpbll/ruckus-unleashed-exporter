FROM python:3.12-slim

LABEL Name="ruckus-unleashed-exporter"
LABEL maintainer="Chris Campbell"

# --- Required: no defaults, container will exit with an error if not set ---
ENV RUCKUS_HOST=""
ENV RUCKUS_PASS=""

# --- Auth ---
ENV RUCKUS_USER=""

# --- Exporter ---
ENV EXPORTER_PORT="9785"

# --- Logging: DEBUG, INFO, WARNING, ERROR ---
ENV LOG_LEVEL="INFO"

# --- Debug: set to 1 to dump raw API responses on first scrape ---
ENV DEBUG_DUMP="0"

WORKDIR /app

RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin exporter

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ruckus_exporter.py .

# Pre-build the bytecode cache as root, because /app stays root-owned and the
# exporter user cannot write __pycache__ at import time
RUN python -m compileall -q /app

USER exporter

EXPOSE 9785

ENTRYPOINT ["python", "-u", "ruckus_exporter.py"]
