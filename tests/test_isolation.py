"""One organization must never see or change another organization's data."""
import io

from conftest import PW


def test_cross_org_access_is_404_or_403(app, make_org, login, add_employee, raw):
    a, b = make_org(), make_org()
    eid = add_employee(a["id"], "X1", name="Only In A")
    cb = login(b["admin"])
    assert b"Only In A" not in cb.get("/employees").data
    assert cb.get(f"/employees/{eid}/edit").status_code == 404
    assert cb.post(f"/employees/{eid}/edit", data={"emp_code": "X1", "name": "Hacked"}).status_code == 404
    assert cb.get("/orgs").status_code == 403
    assert cb.post(f"/orgs/{a['id']}/switch").status_code == 403
    assert cb.get(f"/users/{raw('select id from users where username=:u', u=a['clerk'])[0][0]}/edit").status_code == 404


def test_bulk_assign_cannot_touch_other_org(app, make_org, login, add_employee, raw):
    a, b = make_org(), make_org()
    eid = add_employee(a["id"], "X2")
    cat_b = raw("select id from categories where org_id=:o limit 1", o=b["id"])[0][0]
    before = raw("select category_id from employees where id=:i", i=eid)[0][0]
    login(b["admin"]).post("/categories/bulk-assign", data={"emp_ids": [eid], "category_id": cat_b})
    assert raw("select category_id from employees where id=:i", i=eid)[0][0] == before


def test_attendance_payslips_and_history_are_per_org(app, make_org, login, add_employee, raw):
    a, b = make_org(), make_org()
    add_employee(a["id"], "P1", name="Pay Person")
    ca, cb = login(a["admin"]), login(b["admin"])
    csv = b"emp_code,present_days\nP1,20\n"
    r = ca.post("/attendance", data={"month": "2026-10", "file": (io.BytesIO(csv), "att_a.csv")},
                content_type="multipart/form-data")
    assert b"Saved attendance for 1" in r.data
    ca.post("/payroll/generate", data={"month": "2026-10"})
    assert b"Pay Person" in ca.get("/payroll?month=2026-10").data
    assert b"Pay Person" not in cb.get("/payroll?month=2026-10").data
    assert b"att_a.csv" not in cb.get("/attendance").data
    sid = raw("select id from payslips where org_id=:o", o=a["id"])[0][0]
    assert cb.get(f"/payroll/slip/{sid}.pdf").status_code == 404
    # B cannot post attendance for A's employee code
    r = cb.post("/attendance", data={"month": "2026-10", "file": (io.BytesIO(csv), "att_b.csv")},
                content_type="multipart/form-data")
    assert b"not found" in r.data


def test_platform_admin_must_pick_an_organization(app, make_org, login):
    make_org(); make_org()
    c = app.test_client()
    assert c.post("/login", data={"username": "admin", "password": PW}).status_code == 302
    r = c.get("/employees")
    assert r.status_code == 302 and "/orgs" in r.headers["Location"]
    assert c.get("/orgs").status_code == 200


def test_inactive_org_users_are_locked_out(app, make_org, login):
    org = make_org()
    c = login(org["admin"])
    root = login("admin")
    root.post(f"/orgs/{org['id']}/edit", data={"name": org["name"]})          # is_active unchecked
    assert c.get("/employees").status_code == 302
    r = app.test_client().post("/login", data={"username": org["admin"], "password": PW})
    assert b"inactive" in r.data
    root.post(f"/orgs/{org['id']}/edit", data={"name": org["name"], "is_active": "on"})
