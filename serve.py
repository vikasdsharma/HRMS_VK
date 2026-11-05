"""Production web server (Waitress: pure Python, works on Windows and Linux).  python serve.py
On Linux/Docker the container uses gunicorn instead (see deploy/entrypoint.sh)."""
import logging
import os

import config
from app import app

log = logging.getLogger("hrms")

if __name__ == "__main__":
    from waitress import serve
    log.info("HRMS %s listening on http://%s:%s  (database: %s)", config.ENV, config.HOST, config.PORT,
             config.DATABASE_URL.split("@")[-1] if "@" in config.DATABASE_URL else config.DATABASE_URL)
    serve(app, host=config.HOST, port=config.PORT, threads=int(os.environ.get("HRMS_THREADS", "8")),
          ident="hrms", max_request_body_size=(config.MAX_UPLOAD_MB + 1) * 1024 * 1024)
