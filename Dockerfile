FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HRMS_ENV=production HRMS_DATA_DIR=/data HRMS_AUTO_MIGRATE=0 \
    HRMS_HOST=0.0.0.0 HRMS_PORT=8000 HRMS_TRUST_PROXY=1

# pg_dump for `manage.py backup` against PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 hrms && mkdir /data && chown hrms:hrms /data

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chown -R hrms:hrms /app
USER hrms

VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"

ENTRYPOINT ["sh", "/app/deploy/entrypoint.sh"]
