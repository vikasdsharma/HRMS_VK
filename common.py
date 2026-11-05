# ruff: noqa: F401
"""Shared imports and helpers for the view modules (views do `from common import *`)."""
import io
import json
import logging
import os
import re
import zipfile
from types import SimpleNamespace
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (abort, flash, g, redirect, render_template, request, send_file, session, url_for)
from sqlalchemy import func, or_
from werkzeug.security import check_password_hash, generate_password_hash

import compliance
import config
import importers
import leave
import payroll
import pdfs
import reports
import xl
from crypto import blind_index
from models import (ApiToken, Attendance, AttendanceRequest, CompOffGrant, EmployeeLeaveOverride, Holiday, LeaveApplication,
                    LeaveLedger, LeavePolicy, LeavePolicyRule, LeaveRollover, LeaveType, Notification, WeeklyOffRule, AttendanceUpload, AuditLog, Category, CategoryRate, DailyAttendance, Employee, EmployeeUpload, Organization,
                    Payslip, Setting, User, DEFAULT_SETTINGS, current_org_id, db, get_settings, seed_org_defaults,
                    set_org, unscoped)

log = logging.getLogger("hrms")
ADMIN_ROLES = ("superadmin", "admin")


def role_required(*roles):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if g.user.role not in roles:
                abort(403)
            return fn(*a, **kw)
        return wrapper
    return deco


admin_required = role_required(*ADMIN_ROLES)


def is_admin():
    return bool(getattr(g, "user", None)) and g.user.role in ADMIN_ROLES


def valid_month(m):
    """YYYY-MM or a 400 error. (A real check, not an assert: asserts vanish under python -O.)"""
    try:
        y, mo = map(int, (m or "").split("-"))
        ok = 2000 < y < 2100 and 1 <= mo <= 12
    except (ValueError, AttributeError):
        ok = False
    if not ok:
        abort(400, "Invalid month")
    return f"{y}-{mo:02d}"


def pdf_response(buf, name):
    return send_file(buf, mimetype="application/pdf", download_name=name)


def xlsx_response(buf, name):
    return send_file(buf, download_name=name, as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def client_ip():
    return request.remote_addr


def audit(action, entity=None, entity_id=None, detail=None, org_id=None, commit=True):
    """Append an audit-trail row. NEVER put personal data values (Aadhaar, PAN, bank no.) in `detail`."""
    try:
        u = getattr(g, "user", None)
        db.add(AuditLog(
            org_id=org_id if org_id is not None else current_org_id(),
            user_id=u.id if u else None, username=u.username if u else None,
            action=action, entity=entity, entity_id=str(entity_id) if entity_id is not None else None,
            detail=(detail or "")[:2000] or None, ip=client_ip() if request else None))
        if commit:
            db.commit()
    except Exception:           # auditing must never break the user's action
        db.rollback()
        log.exception("audit write failed for %s", action)


def month_options(n=24, ahead=0):
    """Newest first: `ahead` future months, the current month, then the previous months (n in total, counting
    from the current month backwards)."""
    t = date.today()
    y, m = t.year, t.month + ahead
    while m > 12:
        y, m = y + 1, m - 12
    out = []
    for _ in range(n + ahead):
        out.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def paginate(query, page, per=25):
    """Returns an object with items, page, pages, total, per (attribute access, so `p.items` is the list)."""
    total = query.count()
    pages = max((total + per - 1) // per, 1)
    page = min(max(int(page or 1), 1), pages)
    items = query.limit(per).offset((page - 1) * per).all()
    return SimpleNamespace(items=items, page=page, pages=pages, total=total, per=per)
