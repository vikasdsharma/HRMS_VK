"""HRMS management commands.   python manage.py <command> --help

  migrate              bring the database schema up to date
  create-superadmin    add a platform administrator
  reset-password       set a new password for any user (they must change it at next login)
  backup               write a consistent database backup now
  restore FILE         replace the database with a backup (SQLite; stop the app first)
  check-keys           prove the encryption key can decrypt every protected value
  rotate-key           re-encrypt all protected values under a new key
  show-employee        decrypt and print one employee's protected numbers (manual recovery; audited)
  purge-uploads        drop stored copies of old employee upload files (retention)
  maintenance          daily housekeeping: scheduled backup + retention purge (run it from a scheduler)
"""
import argparse
import getpass
import os
import sys
from datetime import datetime

os.environ.setdefault("HRMS_AUTO_MIGRATE", "0")      # CLI decides when to migrate

import config  # noqa: E402
import models  # noqa: E402
from models import db, User  # noqa: E402


def _password(prompt="New password: "):
    from security import password_problem
    while True:
        pw = getpass.getpass(prompt)
        problem = password_problem(pw)
        if problem:
            print("  ->", problem)
            continue
        if pw != getpass.getpass("Repeat: "):
            print("  -> passwords differ")
            continue
        return pw


def cmd_migrate(a):
    models.run_migrations()
    n = models.encrypt_existing_pii()
    print(f"Database is up to date. Newly encrypted employee records: {n}")


def cmd_create_superadmin(a):
    from werkzeug.security import generate_password_hash
    models.run_migrations()
    if db.query(User).filter_by(username=a.username).first():
        sys.exit(f"User '{a.username}' already exists")
    db.add(User(username=a.username, pw_hash=generate_password_hash(_password()), role="superadmin",
                must_change_password=False))
    db.commit()
    print(f"Platform admin '{a.username}' created.")


def cmd_reset_password(a):
    from werkzeug.security import generate_password_hash
    u = db.query(User).filter_by(username=a.username).first()
    if not u:
        sys.exit(f"No such user: {a.username}")
    u.pw_hash = generate_password_hash(_password())
    u.must_change_password = True
    u.failed_logins, u.locked_until = 0, None
    db.commit()
    print(f"Password reset for '{a.username}'. They must change it at next login.")


def cmd_backup(a):
    import ops
    print("Backup written:", ops.backup_database(a.label))


def cmd_restore(a):
    import ops
    if not os.path.exists(a.file):
        sys.exit("Backup file not found")
    if input("The application must be STOPPED. Type YES to replace the live database: ") != "YES":
        sys.exit("Cancelled")
    print("Restored into", ops.restore_database(a.file), "(previous copy kept as .before-restore)")


def _employee_rows():
    from sqlalchemy import text
    with models.engine.connect() as c:
        return c.execute(text("SELECT id, org_id, emp_code, aadhaar, pan, bank_acct FROM employees")).fetchall()


def cmd_check_keys(a):
    import crypto
    ok = bad = 0
    for r in _employee_rows():
        for v in (r.aadhaar, r.pan, r.bank_acct):
            if v and crypto.is_encrypted(v):
                try:
                    crypto.decrypt(v)
                    ok += 1
                except crypto.DecryptionError:
                    bad += 1
    print(f"Keys loaded: {len(config.FIELD_KEYS)} | values decrypted OK: {ok} | FAILED: {bad}")
    if bad:
        sys.exit("Some values cannot be decrypted with the configured key(s). Locate the original key "
                 "(HRMS_FIELD_KEY or data/field.key) - see README 'Recovering encrypted data'.")


def cmd_rotate_key(a):
    from cryptography.fernet import Fernet
    from sqlalchemy import text
    import crypto
    import ops
    new = a.new_key or Fernet.generate_key().decode()
    Fernet(new.encode())                                    # validates the key format
    old_keys = list(config.FIELD_KEYS)
    cmd_check_keys(a)                                       # refuse to rotate if current data is unreadable
    print("Backup:", ops.backup_database("pre-rotate"))
    n = 0
    with models.engine.begin() as con:
        for r in _employee_rows():
            vals = {}
            for col in ("aadhaar", "pan", "bank_acct"):
                v = getattr(r, col)
                if v:
                    plain = crypto.decrypt(v, keys=old_keys)
                    vals[col] = crypto.encrypt(plain, keys=[new])
                    if col in ("aadhaar", "pan"):
                        vals[col + "_idx"] = crypto.blind_index(col, plain, keys=[new])
            if vals:
                con.execute(text("UPDATE employees SET " + ", ".join(f"{k}=:{k}" for k in vals) + " WHERE id=:id"),
                            {**vals, "id": r.id})
                n += 1
    print(f"Re-encrypted {n} employee record(s) under the new key.")
    path = os.path.join(config.DATA_DIR, "field.key")
    if os.environ.get("HRMS_FIELD_KEY"):
        print("\nYour key comes from the HRMS_FIELD_KEY environment variable. Change it NOW to:\n\n"
              f"  HRMS_FIELD_KEY={new}\n\nand restart the app. Keep the old key somewhere safe until you have "
              "verified everything (python manage.py check-keys).")
    else:
        if os.path.exists(path):
            os.replace(path, path + "." + datetime.now().strftime("%Y%m%d_%H%M%S") + ".old")
        with open(path, "w") as fh:
            fh.write(new)
        print(f"New key saved to {path}. The previous key file was kept beside it as *.old - store it safely.")
    print("Restart the application, then run: python manage.py check-keys")


def cmd_show_employee(a):
    import crypto
    from sqlalchemy import text
    with models.engine.connect() as c:
        r = c.execute(text("SELECT id, name, aadhaar, pan, bank_acct FROM employees WHERE org_id=:o AND emp_code=:c"),
                      {"o": a.org, "c": a.code}).fetchone()
    if not r:
        sys.exit("Employee not found (check --org id and the employee code)")
    print(f"{a.code}  {r.name}\n  Aadhaar: {crypto.decrypt(r.aadhaar)}\n  PAN: {crypto.decrypt(r.pan)}\n"
          f"  Bank a/c: {crypto.decrypt(r.bank_acct)}")
    from models import AuditLog
    db.add(AuditLog(org_id=a.org, username=f"cli:{getpass.getuser()}", action="employee.cli_decrypt",
                    entity="employee", entity_id=str(r.id), detail="decrypted via manage.py show-employee"))
    db.commit()


def cmd_purge_uploads(a):
    import ops
    print("Stored upload files removed:", ops.purge_stored_uploads(a.days))


def cmd_maintenance(a):
    import ops
    print("Backup written:", ops.backup_database("scheduled"))
    print("Stored upload files removed:", ops.purge_stored_uploads())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate").set_defaults(fn=cmd_migrate)
    s = sub.add_parser("create-superadmin"); s.add_argument("username"); s.set_defaults(fn=cmd_create_superadmin)
    s = sub.add_parser("reset-password"); s.add_argument("username"); s.set_defaults(fn=cmd_reset_password)
    s = sub.add_parser("backup"); s.add_argument("--label", default="manual", choices=["manual", "scheduled"])
    s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("restore"); s.add_argument("file"); s.set_defaults(fn=cmd_restore)
    sub.add_parser("check-keys").set_defaults(fn=cmd_check_keys)
    s = sub.add_parser("rotate-key"); s.add_argument("--new-key"); s.set_defaults(fn=cmd_rotate_key)
    s = sub.add_parser("show-employee"); s.add_argument("--org", type=int, required=True); s.add_argument("code")
    s.set_defaults(fn=cmd_show_employee)
    sub.add_parser("maintenance").set_defaults(fn=cmd_maintenance)
    s = sub.add_parser("purge-uploads"); s.add_argument("--days", type=int); s.set_defaults(fn=cmd_purge_uploads)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
