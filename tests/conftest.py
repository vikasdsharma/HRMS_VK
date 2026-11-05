"""Test harness. Environment is set BEFORE the app is imported, so tests never touch your real database,
secret key or encryption key: everything lives in a throw-away temp folder."""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="hrms_test_")
os.environ["HRMS_DATABASE_URL"] = f"sqlite:///{_tmp}/test.db".replace("\\", "/")
os.environ["HRMS_DATA_DIR"] = os.path.join(_tmp, "data")
os.environ["HRMS_INITIAL_ADMIN_PASSWORD"] = "SuperAdmin#2026x"
os.environ["HRMS_ENV"] = "test"

import pytest  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

import app as appmod  # noqa: E402
import models  # noqa: E402
from models import Category, Employee, Organization, User, db, seed_org_defaults, set_org  # noqa: E402

PW = "Test#Password2026"


@pytest.fixture(scope="session")
def app():
    appmod.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, PROPAGATE_EXCEPTIONS=False)
    with appmod.app.app_context():
        u = db.query(User).filter_by(username="admin").first()
        u.pw_hash = generate_password_hash(PW)
        u.must_change_password = False
        db.commit()
    return appmod.app


_counter = {"n": 0}


def _uniq(prefix):
    _counter["n"] += 1
    return f"{prefix}{_counter['n']}"


@pytest.fixture
def make_org(app):
    """Create an organization with sample categories and an admin + a plain user. Returns a dict."""
    def make(name=None, admin_must_change=False):
        name = name or _uniq("Org")
        with app.app_context():
            org = Organization(name=name, is_active=True)
            db.add(org)
            db.flush()
            admin = User(org_id=org.id, username=f"{name.lower()}_admin", pw_hash=generate_password_hash(PW),
                         role="admin", must_change_password=admin_must_change)
            clerk = User(org_id=org.id, username=f"{name.lower()}_clerk", pw_hash=generate_password_hash(PW), role="user")
            db.add_all([admin, clerk])
            seed_org_defaults(org.id)
            db.commit()
            return dict(id=org.id, name=name, admin=admin.username, clerk=clerk.username)
    return make


@pytest.fixture
def login(app):
    def do(username, password=PW, org_id=None):
        c = app.test_client()
        r = c.post("/login", data={"username": username, "password": password})
        assert r.status_code == 302, f"login failed for {username}"
        if org_id:
            c.post(f"/orgs/{org_id}/switch")
        return c
    return do


@pytest.fixture
def add_employee(app):
    def add(org_id, code, name="Test Person", category="Unskilled", **kw):
        with app.app_context():
            set_org(org_id)
            cat = db.query(Category).filter_by(name=category).first()
            e = Employee(emp_code=code, name=name, category=cat, **kw)
            db.add(e)
            db.commit()
            eid = e.id
            set_org(None)
            return eid
    return add


@pytest.fixture
def raw(app):
    """Run raw SQL against the test DB (to look at what is really stored)."""
    from sqlalchemy import text

    def q(sql, **p):
        with models.engine.connect() as c:
            return c.execute(text(sql), p).fetchall()
    return q


from contextlib import contextmanager  # noqa: E402


@contextmanager
def org_ctx(app, org_id):
    """Run service-level code inside an organization (what the request pipeline does for web requests)."""
    with app.app_context():
        set_org(org_id)
        try:
            yield
        finally:
            set_org(None)


@pytest.fixture
def set_setting(app):
    from models import Setting

    def put(org_id, key, value):
        with org_ctx(app, org_id):
            row = db.query(Setting).filter_by(key=key).first() or Setting(key=key)
            row.value = value
            db.add(row)
            db.commit()
    return put
