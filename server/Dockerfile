FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/matplotlib \
    TESIS_HOST=0.0.0.0 \
    TESIS_PORT=8000 \
    TESIS_DATA_ROOT=/data \
    TESIS_RAW_ROOT=/data/raw \
    TESIS_SERVER_DATA_ROOT=/data/server

WORKDIR /app

RUN apt-get update \
    && apt-get install --no-install-recommends -y git libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY server/requirements-core.txt server/requirements-full.txt /tmp/requirements/
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r /tmp/requirements/requirements-full.txt

COPY . /app

RUN addgroup --system geophone \
    && adduser --system --ingroup geophone --home /app geophone \
    && mkdir -p /data/raw /data/processed /data/server /tmp/matplotlib \
    && chown -R geophone:geophone /app /data /tmp/matplotlib

USER geophone

EXPOSE 8000
VOLUME ["/data/raw", "/data/processed", "/data/server"]

HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=4 \
    CMD python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200"

CMD ["python", "-m", "server"]
