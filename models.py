from sqlalchemy import (create_engine, Column, Integer, String, Float, Boolean, Date,
                        ForeignKey, UniqueConstraint, Text)
from sqlalchemy.orm import declarative_base, relationship, scoped_session, sessionmaker
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "hrms.db")
engine = create_engine(f"sqlite:///{DB_PATH}", future=True)
db = scoped_session(sessionmaker(bind=engine, autoflush=True, expire_on_commit=False))
Base = declarative_base()

DEFAULT_SETTINGS = {
    "company_name": "My Manufacturing Pvt Ltd",
    "company_address": "Sonipat, Haryana",
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
}


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    pw_hash = Column(String, nullable=False)


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String, primary_key=True)
    value = Column(String)


class Category(Base):
    """Salary structure. All amounts are per paid day (daily-wage model)."""
    __tablename__ = "categories"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(String)
    basic_day = Column(Float, default=0)
    da_day = Column(Float, default=0)
    hra_day = Column(Float, default=0)
    other_day = Column(Float, default=0)
    ot_multiplier = Column(Float, default=2.0)   # Factories Act: double rate
    pf_applicable = Column(Boolean, default=True)
    esi_applicable = Column(Boolean, default=True)
    pf_ceiling = Column(Boolean, default=True)    # restrict PF wages to ceiling
    employees = relationship("Employee", back_populates="category")

    @property
    def total_day(self):
        return (self.basic_day or 0) + (self.da_day or 0) + (self.hra_day or 0) + (self.other_day or 0)


class Employee(Base):
    __tablename__ = "employees"
    id = Column(Integer, primary_key=True)
    emp_code = Column(String, unique=True, nullable=False)
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
    aadhaar = Column(String, unique=True)
    pan = Column(String, unique=True)
    uan = Column(String, unique=True)
    esic_no = Column(String, unique=True)
    bank_name = Column(String)
    bank_acct = Column(String)
    ifsc = Column(String)
    category_id = Column(Integer, ForeignKey("categories.id"))
    basic_override_day = Column(Float)           # personal daily basic overriding category
    status = Column(String, default="Active")    # Active / Left
    category = relationship("Category", back_populates="employees")

    @property
    def basic_day(self):
        if self.basic_override_day:
            return self.basic_override_day
        return self.category.basic_day if self.category else 0


class Attendance(Base):
    __tablename__ = "attendance"
    id = Column(Integer, primary_key=True)
    emp_id = Column(Integer, ForeignKey("employees.id"), nullable=False)
    month = Column(String, nullable=False)       # YYYY-MM
    present_days = Column(Float, default=0)
    paid_holidays = Column(Float, default=0)     # weekly offs / paid holidays / paid leave
    ot_hours = Column(Float, default=0)
    advance = Column(Float, default=0)
    other_deduction = Column(Float, default=0)
    employee = relationship("Employee")
    __table_args__ = (UniqueConstraint("emp_id", "month"),)


class Payslip(Base):
    __tablename__ = "payslips"
    id = Column(Integer, primary_key=True)
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
    for row in db.query(Setting).all():
        s[row.key] = row.value
    return s


def init_db():
    from werkzeug.security import generate_password_hash
    Base.metadata.create_all(engine)
    if not db.query(User).first():
        db.add(User(username="admin", pw_hash=generate_password_hash("admin123")))
    if not db.query(Category).first():
        # Sample structures - edit to match actual Haryana minimum-wage notification in force.
        db.add_all([
            Category(name="Unskilled", description="Unskilled daily wager", basic_day=400, da_day=60,
                     hra_day=0, other_day=0),
            Category(name="Semi-Skilled", description="Semi-skilled daily wager", basic_day=450, da_day=70,
                     hra_day=0, other_day=0),
            Category(name="Skilled", description="Skilled daily wager", basic_day=500, da_day=80,
                     hra_day=20, other_day=0),
        ])
    db.commit()
