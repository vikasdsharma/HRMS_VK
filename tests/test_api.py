import api as api_mod
from conftest import PW


def _token(app, login, org, scopes=("write",), name="t"):
    c = login(org["admin"])
    r = c.post("/api-tokens/new", data={"name": name, **{s: "on" for s in scopes}})
    assert r.status_code == 200
    return "hrms_" + r.data.decode().split('<code style="word-break:break-all">hrms_')[1].split("</code>")[0]


def H(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_auth_required_and_errors_are_json(app, make_org, login):
    c = app.test_client()
    r = c.get("/api/v1/employees")
    assert r.status_code == 401 and r.json["error"]["code"] == "unauthorized"
    assert c.get("/api/v1/employees", headers=H("hrms_not-a-real-token")).status_code == 401
    assert c.get("/api/v1/openapi.json").status_code == 200                  # description is public
    r = c.get("/api/v1/nope", headers=H("x"))
    assert r.status_code in (401, 404) and r.is_json


def test_token_is_shown_once_and_only_hash_is_stored(app, make_org, login, raw):
    org = make_org()
    tok = _token(app, login, org, name="gate")
    assert raw("select token_hash from api_tokens where org_id=:o", o=org["id"])[0][0] != tok
    assert tok not in login(org["admin"]).get("/api-tokens").get_data(as_text=True)
    assert app.test_client().get("/api/v1/me", headers=H(tok)).json["token"] == "gate"


def test_read_scope_cannot_write_and_pii_is_gated(app, make_org, login, add_employee):
    org = make_org()
    add_employee(org["id"], "A1", name="Api Person", aadhaar="111122223333", uan="100200300400")
    read = _token(app, login, org, scopes=(), name="r")
    full = _token(app, login, org, scopes=("write", "pii"), name="rw")
    c = app.test_client()
    emp = c.get("/api/v1/employees/A1", headers=H(read)).json
    assert emp["name"] == "Api Person" and emp["uan"] == "100200300400" and "aadhaar" not in emp
    assert c.get("/api/v1/employees/A1", headers=H(full)).json["aadhaar"] == "111122223333"
    assert c.post("/api/v1/employees", headers=H(read), json={"code": "N1"}).status_code == 403


def test_create_update_validate_employee(app, make_org, login, raw):
    org = make_org()
    tok = _token(app, login, org)
    c = app.test_client()
    bad = c.post("/api/v1/employees", headers=H(tok), json={"code": "N1", "name": "New", "category": "Unskilled",
                                                             "aadhaar": "12", "pan": "bad"})
    assert bad.status_code == 422 and "aadhaar" in bad.json["error"]["fields"] and "pan" in bad.json["error"]["fields"]
    assert c.post("/api/v1/employees", headers=H(tok), json={"code": "N1", "name": "New"}).status_code == 422   # no category
    ok = c.post("/api/v1/employees", headers=H(tok), json={"code": "N1", "name": "New Worker", "category": "Unskilled",
                                                            "aadhaar": "999988887777", "doj": "2026-01-05"})
    assert ok.status_code == 201 and ok.json["code"] == "N1"
    assert raw("select aadhaar from employees where org_id=:o and emp_code='N1'", o=org["id"])[0][0].startswith("enc1:")
    assert c.post("/api/v1/employees", headers=H(tok), json={"code": "N1", "name": "Dup", "category": "Unskilled"}).status_code == 409
    up = c.patch("/api/v1/employees/N1", headers=H(tok), json={"designation": "Welder"})
    assert up.status_code == 200 and up.json["designation"] == "Welder"
    assert c.patch("/api/v1/employees/ZZ", headers=H(tok), json={"name": "x"}).status_code == 404
    assert "api:t" in " ".join(r[0] for r in raw("select username from audit_log where org_id=:o", o=org["id"]))


def test_attendance_and_payroll_through_the_api(app, make_org, login, add_employee):
    org = make_org()
    add_employee(org["id"], "A1")
    tok = _token(app, login, org)
    c = app.test_client()
    rows = {"rows": [{"emp_code": "A1", "present_days": 22, "paid_holidays": 4, "ot_hours": 8, "advance": 500}]}
    assert c.put("/api/v1/attendance/2026-10", headers=H(tok), json=rows).json["saved"] == 1
    bad = c.put("/api/v1/attendance/2026-10", headers=H(tok), json={"rows": [{"emp_code": "NOPE", "present_days": 1}]})
    assert bad.status_code == 422 and bad.json["error"]["rows"]
    assert c.post("/api/v1/payroll/2026-10/generate", headers=H(tok)).json["generated"] == 1
    p = c.get("/api/v1/payroll/2026-10", headers=H(tok)).json
    assert p["status"] == "draft" and p["payslips"][0]["net"] == 10848 - 0 and p["totals"]["gross"] == 12880
    assert c.get("/api/v1/attendance/2026-13", headers=H(tok)).status_code == 400
    # finalising in the web app locks the API too
    login(org["admin"]).post("/payroll/finalize", data={"month": "2026-10"})
    assert c.put("/api/v1/attendance/2026-10", headers=H(tok), json=rows).status_code == 409
    assert c.post("/api/v1/payroll/2026-10/generate", headers=H(tok)).status_code == 409


def test_tokens_cannot_cross_organizations_and_revoke_works(app, make_org, login, add_employee, raw):
    a, b = make_org(), make_org()
    add_employee(a["id"], "ONLYA", name="Secret A")
    tok_b = _token(app, login, b, name="b")
    c = app.test_client()
    assert c.get("/api/v1/employees/ONLYA", headers=H(tok_b)).status_code == 404
    assert b"Secret A" not in c.get("/api/v1/employees", headers=H(tok_b)).data
    tid = raw("select id from api_tokens where org_id=:o", o=b["id"])[0][0]
    login(b["admin"]).post(f"/api-tokens/{tid}/revoke")
    assert c.get("/api/v1/me", headers=H(tok_b)).status_code == 401
    # the other organization's admin cannot revoke it
    other = login(a["admin"])
    assert other.post(f"/api-tokens/{tid}/revoke").status_code == 404


def test_rate_limit(app, make_org, login, monkeypatch):
    org = make_org()
    tok = _token(app, login, org, scopes=(), name="rl")
    monkeypatch.setattr(api_mod, "RATE_PER_MIN", 3)
    api_mod._hits.clear()
    c = app.test_client()
    codes = [c.get("/api/v1/me", headers=H(tok)).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3] == 429
