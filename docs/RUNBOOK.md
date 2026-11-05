# HRMS Runbook

Practical procedures for whoever operates the system. Commands are run from the project folder.
Windows: use `.venv\Scripts\python.exe` (or the Python that `setup.bat` found) wherever it says `python`.

## 1. What runs where

| Piece | Single machine (Windows) | Cloud (Docker) |
|-------|--------------------------|----------------|
| Web server | `serve.py` (Waitress), via `run.bat` or the "HRMS Server" scheduled task | `gunicorn` in the `app` container |
| Database | `hrms.db` (SQLite, WAL mode) | PostgreSQL 16 in the `db` container (or a managed service) |
| Secrets | `data/secret_key`, `data/field.key` | `HRMS_SECRET_KEY`, `HRMS_FIELD_KEY` in `.env` / secret manager |
| Logs | `data/logs/` | container stdout + `/data/logs` |
| Backups | `data/backups/` | `/data/backups` volume **and** provider snapshots |
| HTTPS | not needed on `127.0.0.1`; use a reverse proxy otherwise | Caddy (`--profile https`) or your load balancer |

Health: `GET /healthz` -> `{"status":"ok"}` (503 if the database is unreachable). Point your uptime monitor at it.

## 2. Day-to-day

* **Start / stop / restart (Windows):** `run.bat`, `stop.bat`, `restart.bat`. Unattended: `scripts\install_tasks.ps1`.
* **Docker:** `docker compose up -d`, `docker compose logs -f app`, `docker compose restart app`.
* **Look at what happened:** web app *Audit log* (who did what); `data/logs/app.log` and `error.log` (problems);
  `access.log` (requests, with a `rid=` reference that is also shown on every error page).
* **A user reports "Something went wrong (reference abc123...)":** `findstr abc123 data\logs\error.log`
  (Linux: `grep abc123 /data/logs/error.log`) shows the stack trace.
* **Locked-out user:** an admin opens *Users* and presses *Unlock* (or waits 15 minutes).
* **Forgotten platform-admin password:** `python manage.py reset-password admin` (asks for the new one).
* **Add a platform admin:** `python manage.py create-superadmin <name>`.

## 2A. Leave and portal routines

* **Start of every month:** *Leave -> Accrual & year-end* -> preview -> *Create credits* (safe to repeat). If any leave type uses
  "1 day per N days worked", run it again after finalising payroll.
* **Daily/weekly:** clear *Applications* and *Attendance requests* (badges in the menu and on the dashboard).
* **After the leave year ends:** *Accrual & year-end -> Preview closing -> Close year*, **then** run the credits for the new year (they wait until the old year is closed).
* **New joiner:** make sure the date of joining is filled in, then add portal access if wanted.
* **Employee leaves the company:** mark them *Left* (portal access ends automatically); use *Leave reports -> liability* and the employee's ledger for the final settlement.
* **Wrong leave balance:** never edit data. Use *Adjust balance* with a reason; it is recorded in the ledger and the audit log.
* **Portal password problems:** *Employee portal access -> Reset password* (a new temporary password is shown once) or *Unlock*.

## 3. Backups

| Task | Command |
|------|---------|
| Backup now | `python manage.py backup` |
| Daily housekeeping (backup + purge old stored uploads) | `python manage.py maintenance` (scheduled 02:00 by `install_tasks.ps1`; in Docker use a cron/`docker exec`) |
| Restore SQLite | stop the app -> `python manage.py restore <file>` -> start |
| Restore PostgreSQL | `psql "<url without +psycopg>" < data/backups/hrms_*.sql` into an empty database, then `python manage.py migrate` |

Rules of thumb:

1. **Copy backups off the machine** (cloud storage, another computer). A backup on the same disk is not a backup.
2. **Keep the encryption key separate from the backups**, but *do* keep it (README section 11). A backup without the key
   restores everything except Aadhaar / PAN / bank numbers.
3. **Practise a restore** once now, then every quarter: restore the latest backup on a spare machine, run
   `python manage.py check-keys`, sign in, open a payslip. An untested backup is a hope, not a backup.
4. Keep `HRMS_BACKUP_KEEP` (default 14) in mind: it prunes automatically.

## 4. Upgrading the application

1. `python manage.py backup`
2. Replace the code (`git pull` / copy files). Do not overwrite `data/`, `.env`, or `hrms.db`.
3. Windows: `setup.bat` (installs packages, runs migrations) then `restart.bat`.
   Docker: `docker compose up -d --build` (the container runs `manage.py migrate` before serving).
4. Check `/healthz`, sign in, open the dashboard.

**Rollback:** stop the app, restore the backup from step 1 (section 3), put the previous code back, start. SQLite
also keeps `data/backups/hrms_pre-migrate_*.db`, taken automatically before any migration.

## 5. Moving to the cloud (PostgreSQL)

1. Provision a VM (2 GB RAM is plenty), install Docker, open ports 80/443, point a DNS name at it.
2. `cp .env.example .env`; fill in `POSTGRES_PASSWORD`, `DOMAIN`, a fresh `HRMS_SECRET_KEY`, and **your existing**
   `HRMS_FIELD_KEY` (from `data/field.key` of the current install) so encrypted numbers stay readable.
3. `docker compose --profile https up -d --build`; confirm `https://<domain>/healthz`.
4. Data migration from SQLite: export with `python manage.py backup`, then load into PostgreSQL with a tool such as
   `pgloader` (SQLite -> PostgreSQL), run `python manage.py migrate`, then `python manage.py check-keys`.
   **Rehearse this on a staging copy first**; the PostgreSQL path has not been exercised in this project's test setup.
5. Switch users over, keep the old install read-only for a few weeks, then retire it.

## 6. Key management (summary; full recipes in README section 11)

* The field key encrypts Aadhaar / PAN / bank account numbers. **Back it up separately; losing it is unrecoverable.**
* `python manage.py check-keys` - verify. `python manage.py rotate-key` - rotate. `show-employee` - manual read.
* The session key (`HRMS_SECRET_KEY`) is different: replacing it just signs everyone out.

## 7. Incidents

| Incident | Do this |
|----------|---------|
| **Database file or backup exposed** | Rotate the field key (`rotate-key`); the exposed copy then only holds the old ciphertext, which is useless without the old key **if the key was not exposed too**. If the key was exposed as well, treat Aadhaar/PAN/bank numbers as disclosed and follow your legal breach-notification duties. Change all passwords. |
| **Secret committed to git** (this happened once: `.secret_key` and early `hrms.db`) | Treat as exposed: rotate `HRMS_SECRET_KEY` (delete `data/secret_key`) and change passwords. Remove the file from history only with a deliberate history rewrite (`git filter-repo`) and a force-push, after warning everyone with a clone. `tests/test_repo_hygiene.py` and CI now fail if it happens again. |
| **Suspected unauthorised access** | Audit log (filter by user), `access.log`; disable the account; reset passwords; revoke API tokens; check *Audit log -> All organizations* as platform admin. |
| **Wrong payroll finalised** | Admin: *Payroll -> Unlock* with a reason (logged), fix attendance/rates, re-generate, finalise. If statutory files were already uploaded, correct through the portal's revision process. |
| **App will not start after an upgrade** | Read the last lines of `data/logs/error.log` / the console. Roll back (section 4). A migration error leaves the pre-migration backup in `data/backups/`. |
| **"database is locked"** | Should not occur (WAL + 15 s wait). If it does: only one app instance may use a SQLite file; look for a stray second server (`stop.bat`, then start once). |
| **Lost field key** | Section 11 of the README. Without any copy of the key the encrypted numbers are gone; the rest of the data is intact. Re-enter Aadhaar/PAN/bank numbers (admins can edit employees, or re-upload the Excel list: blank cells keep, filled cells replace). |

## 8. Checklists

**Before going live in the cloud:** HTTPS works and `HRMS_COOKIE_SECURE` is on; default/initial admin password changed;
field key stored in two safe places; `check-keys` OK; backups scheduled **and** copied off-site; one restore rehearsed;
uptime monitor on `/healthz`; every organization admin changed their temporary password; PF/ESI codes entered in each
organization's Settings; salary structures reviewed against the minimum-wage notification.

**Monthly:** leave credits created -> leave applications cleared -> attendance in -> payroll generated -> warnings reviewed -> **finalised** -> statutory files downloaded
after the pre-check -> uploaded and paid by the 15th -> payslips shared.

**Quarterly:** restore rehearsal; review users and API tokens; review the audit log for reveals/exports you do not
recognise; update PF/ESI/bonus/gratuity settings if the law changed.
