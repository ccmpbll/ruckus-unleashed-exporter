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
# aioruckus is temporarily pinned to a git SHA, so pip needs git to resolve it.
# We install and purge in one layer to keep git out of the final image, and both
# this and the pin come back out once there's a release to point at.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && pip install --no-cache-dir -r requirements.txt \
    && apt-get purge -y --auto-remove git \
    && rm -rf /var/lib/apt/lists/*

COPY ruckus_exporter.py .

# Pre-build the bytecode cache as root, because /app stays root-owned and the
# exporter user cannot write __pycache__ at import time
RUN python -m compileall -q /app

USER exporter

EXPOSE 9785

ENTRYPOINT ["python", "-u", "ruckus_exporter.py"]
