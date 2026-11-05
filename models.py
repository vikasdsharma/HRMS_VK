from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import os
import shutil
import sqlite3

from sqlalchemy import (create_engine, Column, Integer, String, Float, Boolean, Date, DateTime, LargeBinary,
                        ForeignKey, UniqueConstraint, Text, event, inspect)
from sqlalchemy.orm import declarative_base, relationship, scoped_session, sessionmaker, Session, with_loader_criteria

import config
from config import BASE_DIR
from crypto import EncryptedString, blind_index
from sqlalchemy import false as sa_false


def make_engine(url=None):
    """Engine for the configured database. SQLite gets WAL + busy timeout + enforced foreign keys,
    so several people saving at once wait their turn instead of failing with 'database is locked'."""
    url = url or config.DATABASE_URL
    if url.startswith("sqlite"):
        eng = create_engine(url, future=True, connect_args={"timeout": 15})

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_con, _rec):
            cur = dbapi_con.cursor()
            if ":memory:" not in url and url != "sqlite://":
                cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=15000")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
        return eng
    return create_engine(url, future=True, pool_pre_ping=True, pool_size=5, max_overflow=5)


engine = make_engine()
db = scoped_session(sessionmaker(bind=engine, autoflush=True, expire_on_commit=False))
Base = declarative_base()

# ---------------------------------------------------------------- tenant scoping
# Every query on a Tenant model is automatically restricted to the active organization,
# and every new Tenant row is stamped with it, so data cannot leak between organizations.
_org = ContextVar("org_id", default=None)


def set_org(org_id):
    _org.set(org_id)


def current_org_id():
    return _org.get()


@contextmanager
def unscoped():
    """Temporarily query across all organizations (platform admin screens only)."""
    tok = _org.set(None)
    try:
        yield
    finally:
        _org.reset(tok)


class Tenant:
    """Marker for models that belong to one organization."""


@event.listens_for(Session, "do_orm_execute")
def _scope_selects(state):
    oid = _org.get()
    if oid is not None and state.is_select:
        state.statement = state.statement.options(*[
            with_loader_criteria(M, lambda cls: cls.org_id == oid, include_aliases=True)
            for M in Tenant.__subclasses__()])


@event.listens_for(Session, "before_flush")
def _stamp_org(session, ctx, instances):
    oid = _org.get()
    for o in session.new:
        if isinstance(o, Tenant) and o.org_id is None:
            if oid is None:
                raise RuntimeError("No active organization - cannot save tenant data")
            o.org_id = oid
    for o in session.dirty:
        if isinstance(o, Tenant) and oid is not None and o.org_id != oid:
            raise RuntimeError("Cross-organization write blocked")


DEFAULT_SETTINGS = {
    "company_name": "",
    "company_address": "",
    "pf_code": "",          # e.g. HRSNP0012345000
    "esi_code": "",         # 17-digit employer code
    "pf_wage_ceiling": "15000",
    "esi_wage_ceiling": "21000",
    "pf_ee_rate": "12",
    "eps_rate": "8.33",
    "edli_rate": "0.5",
    "pf_admin_rate": "0.5",
    "pf_admin_min": "500",
    "esi_ee_rate": "0.75",
    "esi_er_rate": "3.25",
    "bonus_rate": "8.33",              # Payment of Bonus: minimum % of eligible wages
    "bonus_wage_ceiling": "7000",      # monthly wage ceiling used for the bonus calculation (or min. wage if higher)
    "bonus_eligibility_ceiling": "21000",  # employees earning more than this per month are not eligible for bonus
    "gratuity_cap": "2000000",
    "gratuity_min_years": "5",
    "leave_year_start": "1",           # month the leave year starts: 1 = calendar year, 4 = April-March
    "leave_start_date": "",            # first day leave accrual is managed from (set automatically at the first accrual run)
    "leave_sandwich": "0",             # 1 = weekly offs / holidays between leave days also count as leave
    "leave_backdate_days": "7",        # how far back an employee may apply for leave
    "comp_off_expiry_days": "60",      # default life of a comp-off grant
    "attendance_request_days": "30",   # how far back an employee may ask for an attendance correction
}

ROLES = ("superadmin", "admin", "user")


class Organization(Base):
    __tablename__ = "organizations"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    address = Column(String)
    is_active = Column(Boolean, default=True, nullable=False)


class User(Base):
    """superadmin: org_id NULL, can enter any organization.
    admin: manages one organization incl. its users and settings.
    user: day-to-day work inside one organization."""
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True)
    username = Column(String, unique=True, nullable=False)
    pw_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="user")
    is_active = Column(Boolean, default=True, nullable=False)
    failed_logins = Column(Integer, default=0, nullable=False, server_default="0")
    locked_until = Column(DateTime)
    must_change_password = Column(Boolean, default=False, nullable=False, server_default=sa_false())
    last_login_at = Column(DateTime)
    password_changed_at = Column(DateTime)
    employee_id = Column(Integer, ForeignKey("employees.id"), nullable=True)   # role 'employee': the person this login belongs to
    organization = relationship("Organization")
    __table_args__ = (UniqueConstraint("employee_id", name="uq_user_employee"),)


class ApiToken(Tenant, Base):
    """Bearer token for the JSON API. Only a SHA-256 hash is stored; the token itself is shown once."""
    __tablename__ = "api_tokens"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    prefix = Column(String(12), nullable=False)               # first characters, to recognise a token in the list
    scopes = Column(String, nullable=False, default="read")   # comma list of: read, write, pii
    created_by = Column(String)
    created_at = Column(DateTime, default=datetime.now)
    last_used_at = Column(DateTime)
    revoked_at = Column(DateTime)


class AuditLog(Base):
    """Append-only trail of who did what. org_id is NULL for platform-level events (e.g. a failed login)."""
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    at = Column(DateTime, default=datetime.now, nullable=False, index=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    user_id = Column(Integer, nullable=True)
    username = Column(String)
    action = Column(String, nullable=False, index=True)    # e.g. employee.update, payroll.finalize
    entity = Column(String)                                # e.g. employee
    entity_id = Column(String)
    detail = Column(Text)
    ip = Column(String)


class Setting(Tenant, Base):
    __tablename__ = "settings"
    org_id = Column(Integer, ForeignKey("organizations.id"), primary_key=True)
    key = Column(String, primary_key=True)
    value = Column(String)


class Category(Tenant, Base):
    """Salary structure. All amounts are per paid day (daily-wage model)."""
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(String)
    basic_day = Column(Float, default=0)
    da_day = Column(Float, default=0)
    hra_day = Column(Float, default=0)
    other_day = Column(Float, default=0)
    ot_multiplier = Column(Float, default=2.0)   # Factories Act: double rate
    pf_applicable = Column(Boolean, default=True)
    esi_applicable = Column(Boolean, default=True)
    pf_ceiling = Column(Boolean, default=True)    # restrict PF wages to ceiling
    min_wage_day = Column(Float)                  # notified minimum wage per day for this skill category (warning only)
    employees = relationship("Employee", back_populates="category")
    rates = relationship("CategoryRate", back_populates="category", order_by="CategoryRate.effective_from")
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    @property
    def total_day(self):
        return (self.basic_day or 0) + (self.da_day or 0) + (self.hra_day or 0) + (self.other_day or 0)


class CategoryRate(Tenant, Base):
    """Daily rates of a category from a given month onward. Payroll for month M uses the newest row whose
    effective_from <= M, so changing a rate never rewrites earlier months."""
    __tablename__ = "category_rates"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    effective_from = Column(String, nullable=False)          # YYYY-MM
    basic_day = Column(Float, default=0)
    da_day = Column(Float, default=0)
    hra_day = Column(Float, default=0)
    other_day = Column(Float, default=0)
    ot_multiplier = Column(Float, default=2.0)
    category = relationship("Category", back_populates="rates")
    __table_args__ = (UniqueConstraint("category_id", "effective_from", name="uq_rate_cat_from"),)

    @property
    def total_day(self):
        return (self.basic_day or 0) + (self.da_day or 0) + (self.hra_day or 0) + (self.other_day or 0)


class PayrollRun(Tenant, Base):
    """Month status. No row = draft. 'final' months cannot be regenerated or have attendance changed."""
    __tablename__ = "payroll_runs"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    month = Column(String, nullable=False)
    status = Column(String, nullable=False, default="draft")      # draft / final
    finalized_by = Column(String)
    finalized_at = Column(DateTime)
    unlocked_by = Column(String)
    unlocked_at = Column(DateTime)
    note = Column(String)
    __table_args__ = (UniqueConstraint("org_id", "month", name="uq_run_org_month"),)


class Employee(Tenant, Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    emp_code = Column(String, nullable=False)
    name = Column(String, nullable=False)
    father_name = Column(String)
    gender = Column(String)
    dob = Column(Date)
    doj = Column(Date)
    dol = Column(Date)
    designation = Column(String)
    department = Column(String)
    mobile = Column(String)
    address = Column(Text)
    aadhaar = Column(EncryptedString)             # encrypted at rest; see crypto.py
    aadhaar_idx = Column(String(32))              # keyed hash -> uniqueness + exact search
    pan = Column(EncryptedString)
    pan_idx = Column(String(32))
    uan = Column(String)
    esic_no = Column(String)
    bank_name = Column(String)
    bank_acct = Column(EncryptedString)
    ifsc = Column(String)
    category_id = Column(Integer, ForeignKey("categories.id"))
    basic_override_day = Column(Float)           # personal daily basic overriding category
    eps_exempt = Column(Boolean, default=False, nullable=False, server_default=sa_false())  # not eligible for EPS
    status = Column(String, default="Active")    # Active / Left
    category = relationship("Category", back_populates="employees")
    __table_args__ = (UniqueConstraint("org_id", "emp_code", name="uq_emp_code"),
                      UniqueConstraint("org_id", "aadhaar_idx", name="uq_emp_aadhaar"),
                      UniqueConstraint("org_id", "pan_idx", name="uq_emp_pan"),
                      UniqueConstraint("org_id", "uan", name="uq_emp_uan"),
                      UniqueConstraint("org_id", "esic_no", name="uq_emp_esic"))

    @property
    def basic_day(self):
        if self.basic_override_day:
            return self.basic_override_day
        return self.category.basic_day if self.category else 0


@event.listens_for(Session, "before_flush")
def _blind_indexes(session, ctx, instances):
    """Keep aadhaar_idx / pan_idx in step with the (plaintext) attribute values."""
    for o in list(session.new) + list(session.dirty):
        if isinstance(o, Employee):
            o.aadhaar_idx = blind_index("aadhaar", o.aadhaar)
            o.pan_idx = blind_index("pan", o.pan)


class Attendance(Tenant, Base):
    __tablename__ = "attendance"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    emp_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    month = Column(String, nullable=False)       # YYYY-MM
    present_days = Column(Float, default=0)
    paid_holidays = Column(Float, default=0)     # weekly offs / paid holidays / paid leave
    ot_hours = Column(Float, default=0)
    advance = Column(Float, default=0)
    other_deduction = Column(Float, default=0)
    employee = relationship("Employee")
    __table_args__ = (UniqueConstraint("emp_id", "month"),)


class EmployeeUpload(Tenant, Base):
    """History of every employee Excel upload attempt (kept even when rejected)."""
    __tablename__ = "employee_uploads"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    filename = Column(String)
    uploaded_by = Column(String)
    uploaded_at = Column(DateTime, default=datetime.now)
    created = Column(Integer, default=0)
    updated = Column(Integer, default=0)
    status = Column(String)                      # Saved / Rejected
    details = Column(Text)
    file_data = Column(LargeBinary)              # the uploaded file, kept so a fix-and-reupload report can be built
    row_results = Column(Text)                   # JSON: per-row status / error codes (NULL = file unreadable)


class DailyAttendance(Tenant, Base):
    """One mark per employee per day. Status: P present, HD half day, A absent, WO weekly off,
    H paid holiday, L paid leave. Summed into the monthly Attendance row on request."""
    __tablename__ = "daily_attendance"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    emp_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    day = Column(Date, nullable=False)
    status = Column(String(2), nullable=False)
    ot_hours = Column(Float, default=0)
    source = Column(String(10), default="manual", nullable=False, server_default="manual")   # manual / leave
    leave_application_id = Column(Integer, nullable=True)
    leave_type_id = Column(Integer, nullable=True)
    employee = relationship("Employee")
    __table_args__ = (UniqueConstraint("emp_id", "day", name="uq_daily_emp_day"),)


class LeaveType(Tenant, Base):
    __tablename__ = "leave_types"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    code = Column(String(8), nullable=False)
    name = Column(String, nullable=False)
    paid = Column(Boolean, default=True, nullable=False)
    uses_balance = Column(Boolean, default=True, nullable=False)       # False for unpaid leave (LWP)
    half_day_ok = Column(Boolean, default=True, nullable=False)
    doc_after_days = Column(Integer)                                   # certificate needed beyond this many days
    encashable = Column(Boolean, default=False, nullable=False)        # included in the leave-liability report
    active = Column(Boolean, default=True, nullable=False)
    sort_order = Column(Integer, default=0, nullable=False)
    __table_args__ = (UniqueConstraint("org_id", "code", name="uq_leavetype_code"),)


class LeavePolicy(Tenant, Base):
    """A set of leave rules. Applies to one salary category; the policy marked default covers everyone else."""
    __tablename__ = "leave_policies"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    is_default = Column(Boolean, default=False, nullable=False)
    rules = relationship("LeavePolicyRule", back_populates="policy", cascade="all, delete-orphan")


class LeavePolicyRule(Tenant, Base):
    __tablename__ = "leave_policy_rules"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    policy_id = Column(Integer, ForeignKey("leave_policies.id"), nullable=False)
    leave_type_id = Column(Integer, ForeignKey("leave_types.id"), nullable=False)
    quota = Column(Float, default=0, nullable=False)                   # days per leave year
    accrual = Column(String, default="yearly", nullable=False)         # yearly / monthly / per_days_worked / manual
    accrual_days = Column(Float, default=20)                           # per_days_worked: 1 day per this many days worked
    wait_months = Column(Integer, default=0, nullable=False)           # months after joining before leave starts to accrue
    prorate = Column(Boolean, default=True, nullable=False)            # prorate the first (part) year / month
    carry_mode = Column(String, default="none", nullable=False)        # none / all / limit
    carry_limit = Column(Float, default=0)
    max_balance = Column(Float)                                        # ceiling on the balance (NULL = no ceiling)
    expires_days = Column(Integer)                                     # life of a credit (comp off)
    allow_negative = Column(Boolean, default=False, nullable=False)
    policy = relationship("LeavePolicy", back_populates="rules")
    leave_type = relationship("LeaveType")
    __table_args__ = (UniqueConstraint("policy_id", "leave_type_id", name="uq_rule_policy_type"),)


class EmployeeLeaveOverride(Tenant, Base):
    """Per-employee quota / ceiling that replaces the policy value (R2: fixed leaves for each employee)."""
    __tablename__ = "employee_leave_overrides"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    emp_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    leave_type_id = Column(Integer, ForeignKey("leave_types.id"), nullable=False)
    quota = Column(Float)
    max_balance = Column(Float)
    __table_args__ = (UniqueConstraint("emp_id", "leave_type_id", name="uq_override_emp_type"),)


class LeaveLedger(Tenant, Base):
    """Append-only leave transactions. Balance = SUM(days). `ref` makes automatic credits idempotent."""
    __tablename__ = "leave_ledger"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    emp_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    leave_type_id = Column(Integer, ForeignKey("leave_types.id"), nullable=False)
    on_date = Column(Date, nullable=False)
    kind = Column(String, nullable=False)       # opening credit adjustment debit debit_reversal lapse comp_grant comp_expiry
    days = Column(Float, nullable=False)
    leave_year = Column(Integer, nullable=False)
    note = Column(String)
    ref = Column(String)
    application_id = Column(Integer)
    created_by = Column(String)
    created_at = Column(DateTime, default=datetime.now)
    employee = relationship("Employee")
    leave_type = relationship("LeaveType")
    __table_args__ = (UniqueConstraint("org_id", "emp_id", "leave_type_id", "ref", name="uq_ledger_ref"),)


class LeaveApplication(Tenant, Base):
    __tablename__ = "leave_applications"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    emp_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    leave_type_id = Column(Integer, ForeignKey("leave_types.id"), nullable=False)
    from_date = Column(Date, nullable=False)
    to_date = Column(Date, nullable=False)
    from_half = Column(Boolean, default=False, nullable=False)
    to_half = Column(Boolean, default=False, nullable=False)
    days = Column(Float, nullable=False)
    reason = Column(String)
    doc_provided = Column(Boolean, default=False, nullable=False)
    status = Column(String, default="pending", nullable=False)        # pending approved rejected cancelled
    applied_by = Column(String)
    applied_at = Column(DateTime, default=datetime.now)
    decided_by = Column(String)
    decided_at = Column(DateTime)
    decision_note = Column(String)
    employee = relationship("Employee")
    leave_type = relationship("LeaveType")


class CompOffGrant(Tenant, Base):
    __tablename__ = "comp_off_grants"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    emp_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    worked_on = Column(Date, nullable=False)
    days = Column(Float, nullable=False)
    reason = Column(String, nullable=False)
    granted_by = Column(String)
    granted_at = Column(DateTime, default=datetime.now)
    expires_on = Column(Date, nullable=False)
    employee = relationship("Employee")


class Holiday(Tenant, Base):
    __tablename__ = "holidays"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    on_date = Column(Date, nullable=False)
    name = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("org_id", "on_date", name="uq_holiday_date"),)


class WeeklyOffRule(Tenant, Base):
    """Weekly off days (0=Monday .. 6=Sunday, comma separated). category_id NULL = organization default."""
    __tablename__ = "weekly_off_rules"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    weekdays = Column(String, nullable=False, default="6")


class AttendanceRequest(Tenant, Base):
    """An employee's request to correct a day's attendance; an admin accepts or rejects it."""
    __tablename__ = "attendance_requests"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    emp_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    on_date = Column(Date, nullable=False)
    requested_status = Column(String(2), nullable=False)
    reason = Column(String, nullable=False)
    status = Column(String, default="pending", nullable=False)        # pending approved rejected
    requested_by = Column(String)
    requested_at = Column(DateTime, default=datetime.now)
    decided_by = Column(String)
    decided_at = Column(DateTime)
    decision_note = Column(String)
    employee = relationship("Employee")


class LeaveRollover(Tenant, Base):
    """Marks a leave year as closed (carry-forward applied, excess lapsed)."""
    __tablename__ = "leave_rollovers"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    leave_year = Column(Integer, nullable=False)
    run_by = Column(String)
    run_at = Column(DateTime, default=datetime.now)
    summary = Column(String)
    __table_args__ = (UniqueConstraint("org_id", "leave_year", name="uq_rollover_year"),)


class Notification(Tenant, Base):
    """In-app message for an employee (leave decided, correction decided, comp off granted)."""
    __tablename__ = "notifications"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    emp_id = Column(Integer, ForeignKey("employees.id"), nullable=False, index=True)
    text = Column(String, nullable=False)
    url = Column(String)
    created_at = Column(DateTime, default=datetime.now)
    read_at = Column(DateTime)


class AttendanceUpload(Tenant, Base):
    """History of every attendance CSV upload attempt (kept even when rejected)."""
    __tablename__ = "attendance_uploads"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    month = Column(String, nullable=False)       # YYYY-MM the file was uploaded for
    filename = Column(String)
    uploaded_by = Column(String)
    uploaded_at = Column(DateTime, default=datetime.now)
    rows_saved = Column(Integer, default=0)
    status = Column(String)                      # Saved / Rejected
    details = Column(Text)                       # first few error messages when rejected


class Payslip(Tenant, Base):
    __tablename__ = "payslips"
    id = Column(Integer, primary_key=True)
    org_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    emp_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    month = Column(String, nullable=False)
    category_name = Column(String)
    days_in_month = Column(Integer)
    paid_days = Column(Float)
    ncp_days = Column(Float)
    basic = Column(Float, default=0)
    da = Column(Float, default=0)
    hra = Column(Float, default=0)
    other = Column(Float, default=0)
    ot_amount = Column(Float, default=0)
    gross = Column(Integer, default=0)
    epf_wages = Column(Integer, default=0)
    eps_wages = Column(Integer, default=0)
    edli_wages = Column(Integer, default=0)
    pf_ee = Column(Integer, default=0)
    eps_er = Column(Integer, default=0)
    pf_er_diff = Column(Integer, default=0)
    esi_wages = Column(Integer, default=0)
    esi_ee = Column(Integer, default=0)
    esi_er = Column(Integer, default=0)
    advance = Column(Float, default=0)
    other_deduction = Column(Float, default=0)
    total_deduction = Column(Float, default=0)
    net = Column(Integer, default=0)
    employee = relationship("Employee")
    __table_args__ = (UniqueConstraint("emp_id", "month"),)


def get_settings():
    s = dict(DEFAULT_SETTINGS)
    oid = current_org_id()
    if oid is None:
        s["company_name"] = "HRMS"
        return s
    for row in db.query(Setting).all():          # auto-scoped to active org
        s[row.key] = row.value
    if not s["company_name"]:
        org = db.get(Organization, oid)
        s["company_name"] = org.name if org else ""
    return s


def seed_org_defaults(org_id):
    """Sample salary structures for a new organization - edit to current Haryana minimum wages."""
    for name, desc, b, da, hra in (("Unskilled", "Unskilled daily wager", 400, 60, 0),
                                   ("Semi-Skilled", "Semi-skilled daily wager", 450, 70, 0),
                                   ("Skilled", "Skilled daily wager", 500, 80, 20)):
        c = Category(org_id=org_id, name=name, description=desc, basic_day=b, da_day=da, hra_day=hra, other_day=0)
        db.add(c)
        db.flush()
        db.add(CategoryRate(org_id=org_id, category_id=c.id, effective_from="2000-01", basic_day=b, da_day=da,
                            hra_day=hra, other_day=0, ot_multiplier=2.0))
    seed_leave_defaults(org_id)


def seed_leave_defaults(org_id):
    """Leave types, a default policy and the Sunday weekly off for a new organization (all editable)."""
    from leave_defaults import DEFAULT_RULES, DEFAULT_TYPES
    types = {}
    for t in DEFAULT_TYPES:
        lt = LeaveType(org_id=org_id, **t)
        db.add(lt)
        types[t["code"]] = lt
    db.flush()
    pol = LeavePolicy(org_id=org_id, name="Standard", is_default=True)
    db.add(pol)
    db.flush()
    for code, rule in DEFAULT_RULES.items():
        db.add(LeavePolicyRule(org_id=org_id, policy_id=pol.id, leave_type_id=types[code].id, **rule))
    db.add(WeeklyOffRule(org_id=org_id, category_id=None, weekdays="6"))


def _migrate_legacy():
    """Single-company DB -> multi-organization DB. Existing data goes into organization #1.
    A full copy of the old file is kept next to it."""
    path = engine.url.database
    backup = path[:-3] + "_pre_multitenant_backup.db" if path.endswith(".db") else path + ".bak"
    db.remove()
    engine.dispose()
    shutil.copy2(path, backup)
    os.remove(path)
    run_migrations()
    engine.dispose()
    con = sqlite3.connect(path)
    try:
        con.execute("ATTACH DATABASE ? AS old", (backup,))
        row = con.execute("SELECT value FROM old.settings WHERE key='company_name'").fetchone()
        name = (row[0] if row and row[0] else "Default Organization")
        con.execute("INSERT INTO organizations(id,name,address,is_active) VALUES (1,?,?,1)", (name, None))

        def cols(schema, table):
            return [r[1] for r in con.execute(f"PRAGMA {schema}.table_info({table})")]

        con.execute("INSERT INTO users(id,org_id,username,pw_hash,role,is_active) "
                    "SELECT id,NULL,username,pw_hash,'superadmin',1 FROM old.users")
        for t in ("settings", "categories", "employees", "attendance", "payslips"):
            c = [x for x in cols("main", t) if x in cols("old", t) and x != "org_id"]
            cl = ",".join(c)
            con.execute(f"INSERT INTO {t}(org_id,{cl}) SELECT 1,{cl} FROM old.{t}")
        con.commit()
    except Exception:
        con.close()
        engine.dispose()
        shutil.copy2(backup, path)       # restore untouched original
        raise
    con.close()


def alembic_config():
    from alembic.config import Config
    cfg = Config(os.path.join(BASE_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BASE_DIR, "migrations"))
    return cfg


def pending_migrations():
    """True when the database is not at the latest migration."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    with engine.connect() as con:
        return MigrationContext.configure(con).get_current_revision() != head


def run_migrations(target="head"):
    """Bring the database schema up to date. On SQLite a safety copy is taken first."""
    from alembic import command
    if pending_migrations() and inspect(engine).get_table_names() and engine.url.drivername.startswith("sqlite"):
        import ops
        ops.backup_database("pre-migrate")
    command.upgrade(alembic_config(), target)


def encrypt_existing_pii(con=None):
    """Convert any plaintext Aadhaar / PAN / bank numbers (rows created before encryption existed) into
    encrypted values + blind indexes. Safe to run repeatedly. Pass a connection to run inside a transaction."""
    from sqlalchemy import text
    from crypto import encrypt, decrypt, is_encrypted
    if con is None:
        with engine.begin() as c:
            return encrypt_existing_pii(c)
    n = 0
    for r in con.execute(text("SELECT id, aadhaar, pan, bank_acct, aadhaar_idx, pan_idx FROM employees")).fetchall():
        vals = {}
        for col in ("aadhaar", "pan", "bank_acct"):
            v = getattr(r, col)
            if v and not is_encrypted(v):
                vals[col] = encrypt(v)
                if col != "bank_acct":
                    vals[col + "_idx"] = blind_index(col, v)
        if r.aadhaar and is_encrypted(r.aadhaar) and not r.aadhaar_idx and "aadhaar_idx" not in vals:
            vals["aadhaar_idx"] = blind_index("aadhaar", decrypt(r.aadhaar))
        if r.pan and is_encrypted(r.pan) and not r.pan_idx and "pan_idx" not in vals:
            vals["pan_idx"] = blind_index("pan", decrypt(r.pan))
        if vals:
            sets = ", ".join(f"{k} = :{k}" for k in vals)
            con.execute(text(f"UPDATE employees SET {sets} WHERE id = :id"), {**vals, "id": r.id})
            n += 1
    return n


def init_db():
    """Create/upgrade the schema, convert legacy data, seed the first platform admin (if none)."""
    import logging
    from werkzeug.security import check_password_hash, generate_password_hash
    log = logging.getLogger("hrms")
    tables = inspect(engine).get_table_names()
    if "employees" in tables and "organizations" not in tables and engine.url.database:
        _migrate_legacy()
    if config.AUTO_MIGRATE:
        run_migrations()
        n = encrypt_existing_pii()
        if n:
            log.warning("Encrypted sensitive fields of %d existing employee record(s)", n)
    if not db.query(User).first():
        import secrets as _s
        from sqlalchemy.exc import IntegrityError
        pw = config.INITIAL_ADMIN_PASSWORD or _s.token_urlsafe(12)
        try:
            db.add(User(username="admin", pw_hash=generate_password_hash(pw), role="superadmin",
                        must_change_password=True))
            db.commit()
        except IntegrityError:           # another worker process created it first - that one prints the password
            db.rollback()
        else:
            if not config.INITIAL_ADMIN_PASSWORD:
                msg = f"FIRST-RUN PLATFORM ADMIN  username: admin   password: {pw}   (you must change it at first login)"
                bar = "=" * len(msg)
                print("\n" + bar + "\n" + msg + "\n" + bar + "\n", flush=True)
    # Anyone still on the old well-known default password must change it
    for u in db.query(User).filter_by(must_change_password=False).all():
        if check_password_hash(u.pw_hash, "admin123"):
            u.must_change_password = True
    db.commit()
