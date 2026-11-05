"""Central configuration. Everything comes from environment variables (see .env.example);
the defaults give a working local development setup.

Secrets (session key, data-encryption key) are NEVER stored in the code folder: they live in
HRMS_DATA_DIR (default ./data, git-ignored) or in environment variables / your cloud secret manager.
"""
import os
import secrets

from cryptography.fernet import Fernet

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path):
    """Tiny .env reader (KEY=VALUE lines). Real environment variables always win."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(os.path.join(BASE_DIR, ".env"))


def _env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _flag(name, default=False):
    return str(_env(name, "1" if default else "0")).lower() in ("1", "true", "yes", "on")


ENV = _env("HRMS_ENV", "development")                     # development | production
IS_PROD = ENV == "production"
DATA_DIR = os.path.abspath(_env("HRMS_DATA_DIR", os.path.join(BASE_DIR, "data")))
LOG_DIR = os.path.join(DATA_DIR, "logs")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
for _d in (DATA_DIR, LOG_DIR, BACKUP_DIR):
    os.makedirs(_d, exist_ok=True)


def _db_url():
    url = _env("HRMS_DATABASE_URL")
    if not url:
        return f"sqlite:///{os.path.join(BASE_DIR, 'hrms.db')}"
    # Accept the common Heroku/Render/Railway spellings
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


DATABASE_URL = _db_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")

HOST = _env("HRMS_HOST", "127.0.0.1")
PORT = int(_env("HRMS_PORT", "5000"))
LOG_LEVEL = _env("HRMS_LOG_LEVEL", "INFO").upper()
AUTO_MIGRATE = _flag("HRMS_AUTO_MIGRATE", True)           # run DB migrations at start-up
MAX_UPLOAD_MB = int(_env("HRMS_MAX_UPLOAD_MB", "10"))
SESSION_MINUTES = int(_env("HRMS_SESSION_MINUTES", "30"))  # idle timeout
COOKIE_SECURE = _flag("HRMS_COOKIE_SECURE", IS_PROD)      # set when served over HTTPS
BACKUP_KEEP = int(_env("HRMS_BACKUP_KEEP", "14"))
LOGIN_MAX_FAILS = int(_env("HRMS_LOGIN_MAX_FAILS", "5"))
LOGIN_LOCK_MINUTES = int(_env("HRMS_LOGIN_LOCK_MINUTES", "15"))
TRUST_PROXY = _flag("HRMS_TRUST_PROXY", False)          # set when behind nginx/Caddy/a cloud load balancer
INITIAL_ADMIN_PASSWORD = _env("HRMS_INITIAL_ADMIN_PASSWORD")      # optional; else a random one is printed once
UPLOAD_RETENTION_DAYS = int(_env("HRMS_UPLOAD_RETENTION_DAYS", "180"))   # stored upload files


def _file_secret(filename, maker):
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(maker())
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    with open(path) as fh:
        return fh.read().strip()


# Flask session-signing key.
SECRET_KEY = _env("HRMS_SECRET_KEY") or _file_secret("secret_key", lambda: secrets.token_hex(32))

# Key that encrypts Aadhaar / PAN / bank account numbers (Fernet). Comma-separated list in
# HRMS_FIELD_KEY: the FIRST key encrypts, all keys can decrypt (this is how key rotation works).
# LOSING THIS KEY MEANS THE ENCRYPTED NUMBERS CANNOT BE RECOVERED - back it up (see README).
FIELD_KEYS = [k.strip() for k in (_env("HRMS_FIELD_KEY") or
                                  _file_secret("field.key", lambda: Fernet.generate_key().decode())).split(",")
              if k.strip()]
