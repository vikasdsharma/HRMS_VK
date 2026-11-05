"""Operational helpers: database backup / restore / retention. Used by manage.py and the app."""
import glob
import logging
import os
import shutil
import sqlite3
import subprocess
from datetime import datetime

import config

log = logging.getLogger("hrms.ops")


def _sqlite_path():
    from sqlalchemy.engine import make_url
    return make_url(config.DATABASE_URL).database


def backup_database(label="manual", keep=None):
    """Consistent online backup. SQLite: uses the sqlite3 backup API (safe while the app runs).
    PostgreSQL: runs pg_dump (must be installed). Returns the backup file path."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs(config.BACKUP_DIR, exist_ok=True)
    if config.IS_SQLITE:
        dest = os.path.join(config.BACKUP_DIR, f"hrms_{label}_{stamp}.db")
        src = sqlite3.connect(_sqlite_path())
        try:
            dst = sqlite3.connect(dest)
            with dst:
                src.backup(dst)
            dst.close()
        finally:
            src.close()
    else:
        dest = os.path.join(config.BACKUP_DIR, f"hrms_{label}_{stamp}.sql")
        url = config.DATABASE_URL.replace("+psycopg", "")
        subprocess.run(["pg_dump", "--no-owner", "--format=plain", "--file", dest, url], check=True)
    log.info("Backup written: %s", dest)
    prune_backups(keep or config.BACKUP_KEEP)
    return dest


def prune_backups(keep):
    """Keep the newest `keep` scheduled backups. 'pre-migrate' safety copies are kept longer (10)."""
    for label, n in (("manual", keep), ("scheduled", keep), ("pre-migrate", max(keep, 10))):
        files = sorted(glob.glob(os.path.join(config.BACKUP_DIR, f"hrms_{label}_*")), reverse=True)
        for old in files[n:]:
            try:
                os.remove(old)
            except OSError:
                pass


def restore_database(backup_file):
    """SQLite only: replace the live database with a backup. STOP THE APP FIRST.
    The current database is saved aside as <name>.before-restore."""
    if not config.IS_SQLITE:
        raise RuntimeError("Restore for PostgreSQL: use psql with the .sql dump (see README).")
    live = _sqlite_path()
    if os.path.exists(live):
        shutil.copy2(live, live + ".before-restore")
    for ext in ("-wal", "-shm"):
        if os.path.exists(live + ext):
            os.remove(live + ext)
    shutil.copy2(backup_file, live)
    return live


def purge_stored_uploads(days=None):
    """Retention: drop the stored copies of old employee-upload files (the history rows stay)."""
    from datetime import timedelta
    from sqlalchemy import text
    from models import engine
    cutoff = datetime.now() - timedelta(days=days or config.UPLOAD_RETENTION_DAYS)
    with engine.begin() as con:
        r = con.execute(text("UPDATE employee_uploads SET file_data = NULL, row_results = NULL "
                             "WHERE uploaded_at < :c AND file_data IS NOT NULL"), {"c": cutoff})
    return r.rowcount
