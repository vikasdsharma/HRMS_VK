import io

from openpyxl import Workbook, load_workbook

from conftest import PW


def test_unauthenticated_users_are_sent_to_login(app):
    c = app.test_client()
    assert c.get("/employees").status_code == 302
    assert "/login" in c.get("/employees").headers["Location"]


def test_healthz_is_public(app):
    r = app.test_client().get("/healthz")
    assert r.status_code == 200 and r.json == {"status": "ok"}


def test_security_headers_and_no_store(app, make_org, login):
    org = make_org()
    c = login(org["admin"])
    r = c.get("/employees")
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["X-Request-ID"]
    assert "HttpOnly" in c.post("/login", data={"username": org["admin"], "password": PW}).headers["Set-Cookie"]


def test_login_lockout_and_admin_unlock(app, make_org, login):
    org = make_org()
    bad = app.test_client()
    for _ in range(5):
        r = bad.post("/login", data={"username": org["clerk"], "password": "wrong-password-1"})
        assert b"Invalid username or password" in r.data
    r = bad.post("/login", data={"username": org["clerk"], "password": PW})       # correct pw, but locked
    assert b"locked" in r.data and r.status_code == 200
    admin = login(org["admin"])
    from models import User, db
    with app.app_context():
        uid = db.query(User).filter_by(username=org["clerk"]).first().id
    assert admin.post(f"/users/{uid}/unlock").status_code == 302
    assert bad.post("/login", data={"username": org["clerk"], "password": PW}).status_code == 302


def test_unknown_user_gets_same_message(app):
    r = app.test_client().post("/login", data={"username": "nobody-here", "password": "x" * 12})
    assert b"Invalid username or password" in r.data


def test_open_redirect_is_blocked(app, make_org):
    org = make_org()
    for evil in ("//evil.example", "/\\evil.example", "http://evil.example", "https://evil.example/x"):
        r = app.test_client().post(f"/login?next={evil}", data={"username": org["admin"], "password": PW})
        assert r.status_code == 302
        assert "evil.example" not in r.headers["Location"], evil
    r = app.test_client().post("/login?next=/employees", data={"username": org["admin"], "password": PW})
    assert r.headers["Location"].endswith("/employees")


def test_csrf_is_enforced_when_enabled(app, make_org):
    org = make_org()
    c = app.test_client()
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        r = c.post("/login", data={"username": org["admin"], "password": PW})
        assert r.status_code == 400 and b"Session expired" in r.data
        page = c.get("/login").data.decode()
        token = page.split('name="csrf_token" value="')[1].split('"')[0]
        r = c.post("/login", data={"username": org["admin"], "password": PW, "csrf_token": token})
        assert r.status_code == 302
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_forced_password_change_and_policy(app, make_org, login):
    org = make_org(admin_must_change=True)
    c = login(org["admin"])
    r = c.get("/employees")
    assert r.status_code == 302 and "/password" in r.headers["Location"]
    for bad in ("short1", "alllettersnodigits", "1234567890123", "password123", org["admin"] + "9999"):
        r = c.post("/password", data={"old": PW, "new": bad, "confirm": bad})
        assert r.status_code == 200, bad                       # re-shown with an error, not accepted
    r = c.post("/password", data={"old": PW, "new": "Fresh#Pass2026", "confirm": "Different#Pass2026"})
    assert r.status_code == 200
    r = c.post("/password", data={"old": PW, "new": "Fresh#Pass2026", "confirm": "Fresh#Pass2026"})
    assert r.status_code == 302
    assert c.get("/employees").status_code == 200


def test_logout_requires_post(app, make_org, login):
    c = login(make_org()["admin"])
    assert c.get("/logout").status_code == 405
    assert c.post("/logout").status_code == 302
    assert c.get("/employees").status_code == 302


def test_roles_restrict_sensitive_downloads_and_reveal(app, make_org, login, add_employee):
    org = make_org()
    add_employee(org["id"], "R1", aadhaar="111122223333")
    clerk, admin = login(org["clerk"]), login(org["admin"])
    for url in ("/employees/export.xlsx", "/compliance/ecr.txt?month=2026-10", "/compliance/esi.xlsx?month=2026-10",
                "/compliance/epf-challan.pdf?month=2026-10", "/payroll/register.xlsx?month=2026-10",
                "/users", "/settings", "/audit"):
        assert clerk.get(url).status_code == 403, url
    assert admin.get("/employees/export.xlsx").status_code == 200
    # the clerk asking for ?reveal=1 still sees masked values
    html = clerk.get("/employees?reveal=1").data.decode()
    assert "111122223333" not in html and "XXXXXX223333" in html
    assert "111122223333" in admin.get("/employees?reveal=1").data.decode()


def test_formula_injection_is_neutralised_in_exports(app, make_org, login, add_employee):
    org = make_org()
    add_employee(org["id"], "F1", name='=HYPERLINK("http://evil.example","x")')
    wb = load_workbook(io.BytesIO(login(org["admin"]).get("/employees/export.xlsx").data))
    cell = [c for row in wb.active.iter_rows() for c in row if c.value and "HYPERLINK" in str(c.value)][0]
    assert cell.data_type == "s", "name starting with '=' must be stored as text, not a formula"


def test_bad_upload_files_are_rejected_politely(app, make_org, login):
    org = make_org()
    c = login(org["admin"])
    r = c.post("/employees/upload", data={"file": (io.BytesIO(b"this is not excel"), "x.xlsx")},
               content_type="multipart/form-data")
    assert r.status_code == 200 and b"not an Excel" in r.data
    r = c.post("/employees/upload", data={"file": (io.BytesIO(b"PK\x03\x04junk"), "y.xlsx")},
               content_type="multipart/form-data")
    assert r.status_code == 200 and b"not a valid Excel" in r.data


def test_audit_trail_records_actions_without_personal_data(app, make_org, login, raw):
    org = make_org()
    c = login(org["admin"])
    cat_id = raw("select id from categories where org_id=:o limit 1", o=org["id"])[0][0]
    c.post("/employees/new", data={"emp_code": "A1", "name": "Audit Person", "aadhaar": "999988887777",
                                   "pan": "AAAAA1111A", "category_id": cat_id, "status": "Active"})
    rows = raw("select action, detail from audit_log where org_id=:o", o=org["id"])
    actions = {r[0] for r in rows}
    assert {"auth.login", "employee.create"} <= actions
    blob = " ".join(str(r) for r in rows)
    assert "999988887777" not in blob and "AAAAA1111A" not in blob
    assert c.get("/audit").status_code == 200
    # users of another org never see it
    other = login(make_org()["admin"])
    assert b"employee.create" not in other.get("/audit").data
