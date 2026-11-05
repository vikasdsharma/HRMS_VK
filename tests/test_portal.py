"""Employee self-service portal: access granted by an admin, strictly limited to the employee's own data."""
import csv
import io
from datetime import date, timedelta

import pytest

from conftest import org_ctx
from models import LeaveApplication, Notification, db

TEMP = "Temp#Portal2026"
NEW = "Mine#Portal2026x"


def _future_workday(n=14):
    d = date.today() + timedelta(days=n)
    while d.weekday() == 6:
        d += timedelta(days=1)
    return d


@pytest.fixture
def world(app, make_org, login, add_employee, raw):
    """One organization with two employees; employee 1 already has a portal login and has changed the temp password."""
    org = make_org()
    add_employee(org["id"], "E1", name="Ramesh Kumar", doj=date(2020, 1, 1), aadhaar="111122223333", uan="100200300400")
    add_employee(org["id"], "E2", name="Suresh Verma", doj=date(2020, 1, 1), aadhaar="444455556666")
    ids = {r[0]: r[1] for r in raw("select emp_code, id from employees where org_id=:o", o=org["id"])}
    admin = login(org["admin"])
    cl = raw("select id from leave_types where org_id=:o and code='CL'", o=org["id"])[0][0]
    for code in ("E1", "E2"):
        admin.post(f"/leave/employee/{ids[code]}/adjust", data={"leave_type_id": cl, "days": "6", "reason": "opening", "kind": "opening"})
    uname = f"ramesh.{org['id']}"                                  # usernames are unique across the whole system
    r = admin.post(f"/portal-access/{ids['E1']}/grant", data={"username": uname, "password": TEMP}, follow_redirects=True)
    assert b"Portal login created" in r.data
    emp = app.test_client()
    assert emp.post("/login", data={"username": uname, "password": TEMP}).status_code == 302
    return dict(org=org, ids=ids, admin=admin, emp=emp, cl=cl, user=uname)


def _set_password(emp):
    r = emp.post("/password", data={"old": TEMP, "new": NEW, "confirm": NEW})
    assert r.status_code == 302


def test_first_login_forces_password_change_then_portal_works(world):
    emp = world["emp"]
    r = emp.get("/me")
    assert r.status_code == 302 and "/password" in r.headers["Location"]
    _set_password(emp)
    r = emp.get("/me")
    assert r.status_code == 200 and b"Welcome, Ramesh" in r.data
    for url in ("/me/leave", "/me/attendance", "/me/payslips"):
        assert emp.get(url).status_code == 200, url


def test_employee_cannot_reach_any_admin_page(world):
    emp = world["emp"]
    _set_password(emp)
    for url in ("/", "/employees", "/employees/new", f"/employees/{world['ids']['E2']}", "/leave", "/leave/policies", "/settings",
                "/users", "/audit", "/payroll", "/compliance", "/categories", "/attendance", "/attendance/daily", "/portal-access",
                "/api-tokens", "/reports", "/orgs", f"/attendance/employee/{world['ids']['E2']}", "/employees/export.xlsx",
                "/compliance/ecr.txt?month=2026-10", "/payroll/register.xlsx?month=2026-10", "/leave/balances.xlsx"):
        r = emp.get(url)
        assert r.status_code == 302 and "/me" in r.headers["Location"], f"{url} -> {r.status_code}"
    for url in ("/leave/run", "/portal-access/bulk", "/payroll/finalize", "/employees/1/edit"):
        assert emp.post(url, data={"action": "accrue", "month": "2026-10"}).status_code == 302


def test_employee_applies_admin_approves_balance_and_notification(world, raw, app):
    emp, admin, ids, cl = world["emp"], world["admin"], world["ids"], world["cl"]
    _set_password(emp)
    d = _future_workday()
    r = emp.post("/me/leave", data={"leave_type_id": cl, "from_date": d.isoformat(), "reason": "family function"}, follow_redirects=True)
    assert b"sent for approval" in r.data
    app_id, status, applied_by = raw("select id, status, applied_by from leave_applications where emp_id=:e", e=ids["E1"])[0]
    assert status == "pending" and "employee" in applied_by
    # the employee cannot approve their own request
    assert emp.post(f"/leave/applications/{app_id}/approve").status_code == 302
    assert raw("select status from leave_applications where id=:i", i=app_id)[0][0] == "pending"
    assert admin.post(f"/leave/applications/{app_id}/approve", data={"note": "ok"}).status_code == 302
    assert raw("select sum(days) from leave_ledger where emp_id=:e", e=ids["E1"])[0][0] == 5.0
    r = emp.get("/me")
    assert b"was approved" in r.data and b"5" in r.data
    assert emp.post("/me/notifications/read").status_code == 302
    with org_ctx(app, world["org"]["id"]):
        assert db.query(Notification).filter_by(read_at=None).count() == 0
    # future approved leave may be cancelled by the employee; days come back
    assert emp.post(f"/me/leave/{app_id}/cancel").status_code == 302
    assert raw("select sum(days) from leave_ledger where emp_id=:e", e=ids["E1"])[0][0] == 6.0


def test_employee_input_is_validated(world):
    emp, cl = world["emp"], world["cl"]
    _set_password(emp)
    d = _future_workday()
    r = emp.post("/me/leave", data={"leave_type_id": cl, "from_date": d.isoformat(), "to_date": (d + timedelta(days=9)).isoformat()}, follow_redirects=True)
    assert b"available" in r.data                                                          # 7+ working days, only 6 in balance
    old = date.today() - timedelta(days=40)
    r = emp.post("/me/leave", data={"leave_type_id": cl, "from_date": old.isoformat()}, follow_redirects=True)
    assert b"day(s) back" in r.data
    r = emp.post("/me/leave", data={"leave_type_id": cl, "from_date": ""}, follow_redirects=True)
    assert b"Choose the first day" in r.data


def test_employee_sees_and_changes_only_their_own_records(world, raw):
    emp, admin, ids, cl = world["emp"], world["admin"], world["ids"], world["cl"]
    _set_password(emp)
    d = _future_workday(20)
    admin.post(f"/leave/employee/{ids['E2']}/apply", data={"leave_type_id": cl, "from_date": d.isoformat(), "reason": "other person"})
    other_app = raw("select id from leave_applications where emp_id=:e", e=ids["E2"])[0][0]
    assert emp.post(f"/me/leave/{other_app}/cancel").status_code == 404                       # someone else's application
    assert raw("select status from leave_applications where id=:i", i=other_app)[0][0] == "pending"
    html = emp.get("/me/leave").get_data(as_text=True)
    assert "other person" not in html and "Suresh" not in html
    # PII never reaches the portal
    for url in ("/me", "/me/leave", "/me/attendance", "/me/payslips"):
        page = emp.get(url).get_data(as_text=True)
        for secret in ("111122223333", "100200300400", "444455556666", "XXXXXX"):
            assert secret not in page, f"{secret} leaked on {url}"


def test_payslips_only_when_finalised_and_only_own(world, raw):
    emp, admin, ids = world["emp"], world["admin"], world["ids"]
    _set_password(emp)
    admin.post("/attendance", data={"month": "2026-10", "file": (io.BytesIO(b"emp_code,present_days\nE1,20\nE2,22\n"), "a.csv")},
               content_type="multipart/form-data")
    admin.post("/payroll/generate", data={"month": "2026-10"})
    slip1 = raw("select id from payslips where emp_id=:e", e=ids["E1"])[0][0]
    slip2 = raw("select id from payslips where emp_id=:e", e=ids["E2"])[0][0]
    assert b"No payslips available" in emp.get("/me/payslips").data                          # draft: hidden
    assert emp.get(f"/me/payslips/{slip1}.pdf").status_code == 404
    admin.post("/payroll/finalize", data={"month": "2026-10"})
    page = emp.get("/me/payslips").get_data(as_text=True)
    assert "October 2026" in page
    r = emp.get(f"/me/payslips/{slip1}.pdf")
    assert r.status_code == 200 and r.data.startswith(b"%PDF")
    assert emp.get(f"/me/payslips/{slip2}.pdf").status_code == 404                           # a colleague's payslip


def test_attendance_calendar_and_correction_request(world, raw):
    emp, admin, ids = world["emp"], world["admin"], world["ids"]
    _set_password(emp)
    page = emp.get("/me/attendance").get_data(as_text=True)
    assert "Ask for a correction" in page and "Mark one day" not in page and "Mark a range" not in page
    d = date.today() - timedelta(days=2)
    while d.weekday() == 6:
        d -= timedelta(days=1)
    r = emp.post("/me/attendance", data={"date": d.isoformat(), "status": "P", "reason": "supervisor forgot to mark"}, follow_redirects=True)
    assert b"request was sent" in r.data
    rid = raw("select id from attendance_requests where emp_id=:e", e=ids["E1"])[0][0]
    assert emp.post(f"/requests/{rid}/approve").status_code == 302                              # employees cannot decide
    assert raw("select status from attendance_requests where id=:i", i=rid)[0][0] == "pending"
    assert b"Attendance requests" in admin.get("/").data or admin.get("/requests").status_code == 200
    admin.post(f"/requests/{rid}/approve", data={"note": "verified"})
    assert raw("select status from daily_attendance where emp_id=:e and day=:d", e=ids["E1"], d=d.isoformat())[0][0] == "P"
    assert b"approved" in emp.get("/me/attendance").data
    # a change that a request cannot make
    r = emp.post("/me/attendance", data={"date": d.isoformat(), "status": "A", "reason": "x"}, follow_redirects=True)
    assert b"Present or Half day" in r.data


def test_admin_manages_access_disable_enable_reset_unlock(world, app):
    emp, admin, ids = world["emp"], world["admin"], world["ids"]
    _set_password(emp)
    admin.post(f"/portal-access/{ids['E1']}/disable")
    fresh = app.test_client()
    r = fresh.post("/login", data={"username": world["user"], "password": NEW})
    assert b"disabled" in r.data
    assert emp.get("/me").status_code == 302                                                    # live session ends too
    admin.post(f"/portal-access/{ids['E1']}/enable")
    assert fresh.post("/login", data={"username": world["user"], "password": NEW}).status_code == 302
    r = admin.post(f"/portal-access/{ids['E1']}/reset", data={"password": "Reset#Portal2026"}, follow_redirects=True)
    assert b"New temporary password" in r.data
    again = app.test_client()
    assert again.post("/login", data={"username": world["user"], "password": NEW}).get_data(as_text=True).count("Invalid username") == 1
    assert again.post("/login", data={"username": world["user"], "password": "Reset#Portal2026"}).status_code == 302
    assert again.get("/me").status_code == 302                                                  # must change the reset password first


def test_leaving_employee_loses_access(world, app, raw):
    emp, ids = world["emp"], world["ids"]
    _set_password(emp)
    assert emp.get("/me").status_code == 200
    with org_ctx(app, world["org"]["id"]):
        from models import Employee
        db.get(Employee, ids["E1"]).status = "Left"
        db.commit()
    assert emp.get("/me").status_code == 302 and "/login" in emp.get("/me").headers["Location"]
    r = app.test_client().post("/login", data={"username": world["user"], "password": NEW})
    assert b"not active" in r.data


def test_grant_validation_and_uniqueness(world, raw):
    admin, ids = world["admin"], world["ids"]
    assert b"already has portal access" in admin.post(f"/portal-access/{ids['E1']}/grant", data={"username": "again.x", "password": TEMP}, follow_redirects=True).data
    assert b"already taken" in admin.post(f"/portal-access/{ids['E2']}/grant", data={"username": world["user"].upper(), "password": TEMP}, follow_redirects=True).data
    assert b"at least 10 characters" in admin.post(f"/portal-access/{ids['E2']}/grant", data={"username": "s.x", "password": "short"}, follow_redirects=True).data
    assert raw("select count(*) from users where employee_id=:e", e=ids["E2"])[0][0] == 0
    ok = admin.post(f"/portal-access/{ids['E2']}/grant", data={"username": "", "password": ""}, follow_redirects=True)        # defaults
    assert b"Portal login created" in ok.data
    assert raw("select username from users where employee_id=:e", e=ids["E2"])[0][0] == f"e2.{world['org']['id']}"


def test_bulk_credentials_work_and_are_not_stored_in_clear(world, app, add_employee, raw):
    admin, org = world["admin"], world["org"]
    add_employee(org["id"], "E3", name="Third Person", doj=date(2021, 1, 1))
    add_employee(org["id"], "E4", name="Fourth Person", doj=date(2021, 1, 1))
    r = admin.post("/portal-access/bulk")
    assert r.status_code == 200 and r.headers["Content-Disposition"].startswith("attachment")
    rows = list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
    assert rows[0] == ["Employee code", "Name", "Username", "Temporary password"]
    got = {x[0]: x for x in rows[1:]}
    assert set(got) == {"E2", "E3", "E4"}                                                        # E1 already had a login
    for code in ("E3", "E4"):
        _, _, username, pw = got[code]
        assert app.test_client().post("/login", data={"username": username, "password": pw}).status_code == 302
        stored = raw("select pw_hash from users where username=:u", u=username)[0][0]
        assert pw not in stored and stored.startswith(("scrypt:", "pbkdf2:"))
    assert b"Everyone active already has a login" in admin.post("/portal-access/bulk", follow_redirects=True).data


def test_employee_logins_are_hidden_from_the_users_screen(world, raw):
    admin, ids = world["admin"], world["ids"]
    uid = raw("select id from users where employee_id=:e", e=ids["E1"])[0][0]
    assert world["user"].encode() not in admin.get("/users").data
    assert admin.get(f"/users/{uid}/edit").status_code == 404                                    # cannot be promoted to admin
    assert admin.post(f"/users/{uid}/edit", data={"role": "admin", "is_active": "on"}).status_code == 404
    assert raw("select role from users where id=:i", i=uid)[0][0] == "employee"


def test_two_organizations_do_not_share_portal_users(app, make_org, login, add_employee, raw):
    a, b = make_org(), make_org()
    add_employee(a["id"], "SAME", doj=date(2020, 1, 1))
    add_employee(b["id"], "SAME", doj=date(2020, 1, 1))
    ea = raw("select id from employees where org_id=:o", o=a["id"])[0][0]
    eb = raw("select id from employees where org_id=:o", o=b["id"])[0][0]
    ca, cb = login(a["admin"]), login(b["admin"])
    ua, ub = f"same.a{a['id']}", f"same.b{b['id']}"
    ca.post(f"/portal-access/{ea}/grant", data={"username": ua, "password": TEMP})
    cb.post(f"/portal-access/{eb}/grant", data={"username": ub, "password": TEMP})
    emp_a = app.test_client()
    emp_a.post("/login", data={"username": ua, "password": TEMP})
    emp_a.post("/password", data={"old": TEMP, "new": NEW, "confirm": NEW})
    assert emp_a.get("/me").status_code == 200
    page = emp_a.get("/me").get_data(as_text=True)
    assert a["name"] in page and b["name"] not in page                                           # sees only its own organization
    assert raw("select org_id from users where username=:u", u=ua)[0][0] == a["id"]
    # admin of B cannot touch A's employee login
    assert cb.post(f"/portal-access/{ea}/disable").status_code == 404
