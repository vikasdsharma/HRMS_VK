from common import *  # noqa: F401,F403
from web import route

RATE_FIELDS = ("basic_day", "da_day", "hra_day", "other_day")


def _num(name, default=0.0, lo=0.0, hi=100000.0):
    raw = (request.form.get(name) or "").strip()
    v = float(raw) if raw else default
    if not lo <= v <= hi:
        raise ValueError(name)
    return v


@route("/categories")
def categories():
    cats = db.query(Category).order_by(Category.name).all()
    finals = payroll.finalized_months()
    return render_template("categories.html", cats=cats, finals=finals, rate_now=lambda c: payroll.rate_for(c, date.today().strftime("%Y-%m")))


@route("/categories/new", methods=["GET", "POST"])
@route("/categories/<int:cid>/edit", methods=["GET", "POST"])
@admin_required
def category_form(cid=None):
    cat = db.get(Category, cid) if cid else None
    if cid and not cat:
        abort(404)

    def show():
        return render_template("category_form.html", cat=cat, month_opts=month_options(24, ahead=3),
                               rates=list(reversed(cat.rates)) if cat else [], finals=payroll.finalized_months())

    if request.method == "POST":
        name = request.form["name"].strip()
        if not name:
            flash("Name is required", "err")
            return show()
        dup = db.query(Category).filter(func.lower(Category.name) == name.lower())
        if cid:
            dup = dup.filter(Category.id != cid)
        if dup.first():
            flash("A category with that name exists", "err")
            return show()
        try:
            new = {k: _num(k) for k in RATE_FIELDS}
            ot = _num("ot_multiplier", 2.0, 1.0, 10.0)
            min_wage = _num("min_wage_day", 0.0) or None
        except ValueError as e:
            flash(f"{str(e).replace('_', ' ')}: enter a valid amount", "err")
            return show()
        eff = valid_month(request.form.get("effective_from") or date.today().strftime("%Y-%m")) if cat else "2000-01"

        is_new = cat is None
        if is_new:
            cat = Category()
            db.add(cat)
        cat.name = name
        cat.description = request.form.get("description", "").strip()
        cat.pf_applicable = "pf_applicable" in request.form
        cat.esi_applicable = "esi_applicable" in request.form
        cat.pf_ceiling = "pf_ceiling" in request.form
        cat.min_wage_day = min_wage
        db.flush()

        latest = cat.rates[-1] if cat.rates else None
        changed_rates = is_new or latest is None or any(abs((getattr(latest, k) or 0) - v) > 1e-9 for k, v in new.items()) \
            or abs((latest.ot_multiplier or 2) - ot) > 1e-9
        note = ""
        if changed_rates:
            row = next((r for r in cat.rates if r.effective_from == eff), None)
            if row is None:
                row = CategoryRate(category_id=cat.id, effective_from=eff)
                db.add(row)
            for k, v in new.items():
                setattr(row, k, v)
            row.ot_multiplier = ot
            db.flush()
            db.refresh(cat)
            newest = cat.rates[-1]               # category columns always mirror the newest dated rate
            for k in RATE_FIELDS:
                setattr(cat, k, getattr(newest, k))
            cat.ot_multiplier = newest.ot_multiplier
            if not is_new:
                frozen = sorted(m for m in payroll.finalized_months() if m >= eff)
                note = f" New rates apply from {pdfs.month_label(eff)}."
                if frozen:
                    note += f" Finalised months {', '.join(frozen)} are unchanged."
        db.commit()
        audit("category.save", "category", cat.id,
              f"{cat.name}: total/day {cat.total_day}" + (f" effective {eff}" if changed_rates else " (rates unchanged)"))
        flash("Category saved." + note + " Re-generate payslips of open months to apply.", "ok")
        return redirect(url_for("categories"))
    return show()


@route("/categories/<int:cid>/delete", methods=["POST"])
@admin_required
def category_delete(cid):
    cat = db.get(Category, cid)
    if cat and cat.employees:
        flash("Cannot delete: employees are assigned to this category", "err")
    elif cat:
        for r in list(cat.rates):
            db.delete(r)
        db.delete(cat)
        db.commit()
        audit("category.delete", "category", cid, cat.name)
        flash("Category deleted", "ok")
    return redirect(url_for("categories"))


@route("/categories/bulk-assign", methods=["POST"])
def bulk_assign():
    ids = request.form.getlist("emp_ids", type=int)
    cid = request.form.get("category_id", type=int)
    if ids and cid and db.get(Category, cid):
        db.query(Employee).filter(Employee.id.in_(ids), Employee.org_id == current_org_id()) \
            .update({"category_id": cid}, synchronize_session=False)
        db.commit()
        audit("category.bulk_assign", "category", cid, f"{len(ids)} employee(s)")
        flash(f"Category assigned to {len(ids)} employee(s)", "ok")
    else:
        flash("Select employees and a category", "err")
    return redirect(request.referrer or url_for("employees"))
