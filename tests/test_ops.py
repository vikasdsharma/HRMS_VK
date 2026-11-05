"""Migrations stay in step with the models; backup / restore / key rotation actually work."""
import os
import sqlite3
import subprocess
import sys

import config


def test_migrations_match_models(app):
    """If someone changes a model but forgets the Alembic migration, this fails."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    import models
    with models.engine.connect() as con:
        diff = compare_metadata(MigrationContext.configure(con, opts={"compare_type": True}), models.Base.metadata)
    assert diff == [], f"Models and migrations differ - run alembic revision --autogenerate: {diff}"


def test_backup_is_a_readable_database(app, make_org):
    import ops
    make_org()
    path = ops.backup_database("manual")
    con = sqlite3.connect(path)
    assert con.execute("select count(*) from organizations").fetchone()[0] >= 1
    assert con.execute("select version_num from alembic_version").fetchone()[0]
    con.close()


def test_key_rotation_keeps_data_readable(app, make_org, add_employee, raw, tmp_path):
    """Runs manage.py in a SUBPROCESS against a copy of the test database, so the shared test key is untouched."""
    import shutil
    org = make_org()
    add_employee(org["id"], "ROT1", aadhaar="777788889999", pan="ZZZZZ0000Z", bank_acct="11223344556677")
    work = tmp_path / "w"
    (work / "data").mkdir(parents=True)
    import ops
    shutil.copy2(ops.backup_database("manual"), work / "t.db")      # proper online backup (the live file is in WAL mode)
    shutil.copy2(os.path.join(config.DATA_DIR, "field.key"), work / "data" / "field.key")
    env = {**os.environ, "HRMS_DATABASE_URL": f"sqlite:///{(work / 't.db').as_posix()}",
           "HRMS_DATA_DIR": str(work / "data")}
    env.pop("HRMS_FIELD_KEY", None)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def run(*args):
        return subprocess.run([sys.executable, "manage.py", *args], cwd=root, env=env, capture_output=True, text=True)

    before = (work / "data" / "field.key").read_text()
    r = run("check-keys"); assert r.returncode == 0, r.stderr
    r = run("rotate-key"); assert r.returncode == 0, r.stdout + r.stderr
    after = (work / "data" / "field.key").read_text()
    assert after != before
    r = run("check-keys"); assert r.returncode == 0 and "FAILED: 0" in r.stdout, r.stdout + r.stderr
    r = run("show-employee", "--org", str(org["id"]), "ROT1")
    assert "777788889999" in r.stdout and "ZZZZZ0000Z" in r.stdout and "11223344556677" in r.stdout, r.stdout + r.stderr
    # with the OLD key the rotated data must NOT be readable
    (work / "data" / "field.key").write_text(before)
    r = run("check-keys"); assert r.returncode != 0
