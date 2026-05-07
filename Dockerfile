FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/app/config.yaml \
    STATE_PATH=/app/state/state.json

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends tini ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY monitor.py .

RUN useradd --system --uid 1000 monitor && \
    mkdir -p /app/state && chown -R monitor:monitor /app
USER monitor

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/monitor.py"]
