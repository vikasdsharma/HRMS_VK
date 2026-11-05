import pytest

import crypto
from conftest import PW


def test_round_trip_and_blind_index():
    token = crypto.encrypt("123412341234")
    assert token.startswith("enc1:") and "1234" not in token
    assert crypto.encrypt("123412341234") != token                 # random IV: ciphertext differs every time
    assert crypto.decrypt(token) == "123412341234"
    assert crypto.decrypt("legacy-plaintext") == "legacy-plaintext"   # old rows keep working
    assert crypto.blind_index("aadhaar", "1234 1234 1234") == crypto.blind_index("aadhaar", "123412341234")
    assert crypto.blind_index("pan", "abcde1234f") == crypto.blind_index("pan", "ABCDE1234F")
    assert crypto.blind_index("aadhaar", "1") != crypto.blind_index("pan", "1")


def test_wrong_key_fails_loudly():
    from cryptography.fernet import Fernet
    token = crypto.encrypt("secret")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(token, keys=[Fernet.generate_key().decode()])


def test_key_rotation_old_data_still_readable():
    from cryptography.fernet import Fernet
    old, new = crypto.config.FIELD_KEYS[0], Fernet.generate_key().decode()
    token = crypto.encrypt("value", keys=[old])
    assert crypto.decrypt(token, keys=[new, old]) == "value"       # new key first, old kept for decrypting


def test_sensitive_fields_are_encrypted_in_the_database(app, make_org, login, raw):
    org = make_org()
    c = login(org["admin"])
    cat_id = raw("select id from categories where org_id=:o limit 1", o=org["id"])[0][0]
    c.post("/employees/new", data={"emp_code": "E1", "name": "Enc Test", "aadhaar": "555566667777",
                                   "pan": "ABCDE9999Z", "bank_acct": "9988776655", "uan": "100200300400",
                                   "category_id": cat_id, "status": "Active"})
    a, p, b, u, ai = raw("select aadhaar, pan, bank_acct, uan, aadhaar_idx from employees "
                         "where org_id=:o and emp_code='E1'", o=org["id"])[0]
    assert a.startswith("enc1:") and p.startswith("enc1:") and b.startswith("enc1:")
    assert "5555" not in a and ai and len(ai) == 32
    assert u == "100200300400"                                     # UAN stays searchable plain text (needed for ECR)
    assert "555566667777" in c.get("/employees?reveal=1").data.decode()


def test_unique_per_org_and_exact_search(app, make_org, login, raw):
    a, b = make_org(), make_org()
    ca, cb = login(a["admin"]), login(b["admin"])
    cat_a = raw("select id from categories where org_id=:o limit 1", o=a["id"])[0][0]
    cat_b = raw("select id from categories where org_id=:o limit 1", o=b["id"])[0][0]
    ok = ca.post("/employees/new", data={"emp_code": "U1", "name": "One", "aadhaar": "121212121212",
                                         "category_id": cat_a, "status": "Active"}, follow_redirects=True)
    assert b"Saved One" in ok.data
    dup = ca.post("/employees/new", data={"emp_code": "U2", "name": "Two", "aadhaar": "1212 1212 1212",
                                          "category_id": cat_a, "status": "Active"}, follow_redirects=True)
    assert b"already belongs" in dup.data                            # same Aadhaar (spaces ignored) blocked in the org
    same_other_org = cb.post("/employees/new", data={"emp_code": "U1", "name": "Other", "aadhaar": "121212121212",
                                                     "category_id": cat_b, "status": "Active"}, follow_redirects=True)
    assert b"Saved Other" in same_other_org.data                     # but fine in another organization
    assert b"One" in ca.get("/employees?q=121212121212").data
    assert b"Other" not in ca.get("/employees?q=121212121212").data


def test_blank_in_hidden_mode_keeps_value_and_reveal_mode_clears(app, make_org, login, raw):
    org = make_org()
    c = login(org["admin"])
    cat_id = raw("select id from categories where org_id=:o limit 1", o=org["id"])[0][0]
    c.post("/employees/new", data={"emp_code": "K1", "name": "Keep", "aadhaar": "343434343434",
                                   "category_id": cat_id, "status": "Active"})
    eid = raw("select id from employees where org_id=:o and emp_code='K1'", o=org["id"])[0][0]
    base = {"emp_code": "K1", "name": "Keep", "category_id": cat_id, "status": "Active", "aadhaar": ""}
    c.post(f"/employees/{eid}/edit", data=base)
    assert raw("select aadhaar_idx from employees where id=:i", i=eid)[0][0] is not None
    c.post(f"/employees/{eid}/edit", data={**base, "reveal": "1"})
    assert raw("select aadhaar, aadhaar_idx from employees where id=:i", i=eid)[0] == (None, None)
