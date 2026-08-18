FROM python:3.12-slim

# tzdata lets the TZ env var (from .env) govern container log timestamps
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app/ app/
COPY scripts/ scripts/

ENV DATA_DIR=/data
VOLUME /data
EXPOSE 5000 5001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD \
  python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('WEBHOOK_PORT','5000')+'/health')"

CMD ["python", "-m", "app.main"]
