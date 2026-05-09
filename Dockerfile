FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends tini \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --uid 1000 --create-home --shell /bin/bash app

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY monitor.py webui.py utils.py entrypoint.sh ./
COPY templates ./templates
COPY static ./static
RUN chmod +x entrypoint.sh

USER app
EXPOSE 8088

ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["/app/entrypoint.sh"]
