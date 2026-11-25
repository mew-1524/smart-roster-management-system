"""Use-case level operations shared by views, the seed command and tests."""
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.models import (Assignment, Brand, Conflict, Employee, Leave, RosterAssignment, RosterEntry, RosterPeriod,
                              log_activity)

from .calendar_rules import MonthContext
from .engine import generate_roster
from .explain import candidate_replacements, entry_pairs, load_entry_map
from .validation import run_validation


def months_between(start, end):
    out, d = [], date(start.year, start.month, 1)
    while d <= end:
        out.append((d.year, d.month))
        d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return out


def revalidate_existing():
    """Re-run validation for every month that already has a roster (called after any data change)."""
    for p in RosterPeriod.objects.all():
        run_validation(p.year, p.month)


def valid_cover(ctx, emap, pair, d, exclude=None):
    n = 0
    for emp_id in ctx.employees:
        if emp_id == exclude or ctx.unavailable(emp_id, d):
            continue
        if pair in entry_pairs(emap.get((emp_id, d))) and (pair in ctx.emp_pairs[emp_id] or pair in ctx.emp_backup_pairs[emp_id]):
            n += 1
    return n


def check_proposed(ctx, emap, emp, d, status, pairs):
    """Pre-save validation of a manual edit. Returns [(severity, message)]."""
    issues = []
    if status == "WORKING":
        reason = ctx.unavailable(emp.id, d)
        if reason:
            issues.append(("HIGH", f"{emp.full_name} is unavailable on {d:%d %b} ({reason[1].lower()})."))
        if not pairs:
            issues.append(("LOW", "Status is Working but no brand/process is selected."))
        if len(pairs) > ctx.settings.max_daily_workloads:
            issues.append(("MEDIUM", f"More than {ctx.settings.max_daily_workloads} workloads on one day."))
        for i, p in enumerate(pairs):
            if p not in ctx.emp_pairs[emp.id] and p not in ctx.emp_backup_pairs[emp.id]:
                issues.append(("HIGH", f"{emp.full_name} is not assigned to {ctx.pair_label(p)}."))
            elif ctx.required(ctx.pair_rule[p], d) == 0:
                issues.append(("MEDIUM", f"{ctx.pair_label(p)} is closed on {d:%a %d %b}."))
            if p in ctx.pair_rule and ctx.shift_clash(pairs[:i], p):
                issues.append(("HIGH", f"Incompatible shifts combined for {ctx.pair_label(p)}."))
    else:
        if status in ("LEAVE", "COMP_OFF") and (emp.id, d) not in ctx.blocked:
            issues.append(("LOW", f"No approved {'leave' if status == 'LEAVE' else 'comp-off'} exists for {d:%d %b}; add one in Availability & Leaves."))
        for p in ctx.emp_pairs[emp.id]:
            rule = ctx.pair_rule[p]
            need = ctx.required(rule, d)
            if need and valid_cover(ctx, emap, p, d, exclude=emp.id) < need:
                issues.append(("CRITICAL", f"Taking {emp.full_name} off leaves {ctx.pair_label(p)} below its minimum of {need} on {d:%a %d %b}."))
    return issues


@transaction.atomic
def save_manual_entry(user, emp, d, status, pairs, note=""):
    entry, _ = RosterEntry.objects.update_or_create(employee=emp, date=d, defaults={"status": status, "source": "MANUAL", "note": note})
    entry.assignments.all().delete()
    if status == "WORKING":
        RosterAssignment.objects.bulk_create([RosterAssignment(entry=entry, brand_id=b, process_id=p) for b, p in dict.fromkeys(pairs)])
    RosterPeriod.objects.get_or_create(year=d.year, month=d.month)
    log_activity(user, f"Manual roster edit: {emp.employee_id} on {d} → {entry.get_status_display()}")
    return run_validation(d.year, d.month)


@transaction.atomic
def assign_cover(user, emp, d, brand_id, process_id):
    entry = RosterEntry.objects.filter(employee=emp, date=d).first()
    if entry is None:
        entry = RosterEntry.objects.create(employee=emp, date=d, status="WORKING", source="MANUAL", note="Manual cover")
    else:
        entry.status, entry.source, entry.note = "WORKING", "MANUAL", "Manual cover"
        entry.save()
    RosterAssignment.objects.get_or_create(entry=entry, brand_id=brand_id, process_id=process_id, defaults={"is_cover": True})
    # Covering for a brand/process makes the person an (explicit) backup resource for it, so the roster stays valid.
    Assignment.objects.get_or_create(employee=emp, brand_id=brand_id, process_id=process_id,
                                     defaults={"is_backup": True, "is_active": True, "assigned_date": d})
    log_activity(user, f"Assigned {emp.employee_id} as cover on {d} ({brand_id}/{process_id})")
    return run_validation(d.year, d.month)


@transaction.atomic
def apply_leave_to_roster(user, leave):
    """Syncs a roster that was generated before the leave existed: working rows become LEAVE."""
    n = 0
    for en in RosterEntry.objects.filter(employee=leave.employee, date__range=(leave.start_date, leave.end_date), status="WORKING"):
        en.assignments.all().delete()
        en.status, en.note = "LEAVE", "Approved leave applied to roster"
        en.save()
        n += 1
    log_activity(user, f"Applied leave of {leave.employee.employee_id} to roster ({n} days)")
    for y, m in months_between(leave.start_date, leave.end_date):
        run_validation(y, m)
    return n


def leave_impact(leave):
    """What-if analysis: for each affected day and each workload of the employee, can the others still cover?"""
    rows, notes = [], []
    for y, m in months_between(leave.start_date, leave.end_date):
        ctx = MonthContext(y, m)
        emap = load_entry_map(ctx)
        if not emap:
            notes.append(f"No roster exists for {y}-{m:02d}; generate it to see the coverage impact.")
            continue
        d = max(leave.start_date, ctx.first)
        while d <= min(leave.end_date, ctx.last):
            emp = ctx.employees.get(leave.employee_id)
            for p in sorted(set(ctx.emp_pairs.get(leave.employee_id, [])) | set(ctx.emp_backup_pairs.get(leave.employee_id, []))):
                rule = ctx.pair_rule[p]
                need = ctx.required(rule, d)
                if not need or p not in entry_pairs(emap.get((leave.employee_id, d))):
                    continue
                others = valid_cover(ctx, emap, p, d, exclude=leave.employee_id)
                names = [ctx.employees[e].full_name for e in ctx.employees
                         if e != leave.employee_id and not ctx.unavailable(e, d) and p in entry_pairs(emap.get((e, d)))]
                state = "GAP" if others == 0 else ("SHORT" if others < need else "OK")
                cands = candidate_replacements(ctx, emap, rule, d, limit=3) if state != "OK" else []
                rows.append({"date": d, "label": ctx.pair_label(p), "need": need, "remaining": others, "names": names,
                             "state": state, "candidates": cands, "brand_id": p[0], "process_id": p[1]})
            d += timedelta(days=1)
    return rows, notes


def publish_period(user, year, month):
    period = RosterPeriod.objects.get(year=year, month=month)
    period.status, period.published_at = "PUBLISHED", timezone.now()
    period.save()
    log_activity(user, f"Published roster {year}-{month:02d}")


# ---------------------------------------------------------------- demo scenario
DEMO_TAG = "[DEMO]"


def reset_demo():
    Leave.objects.filter(reason__startswith=DEMO_TAG).delete()
    revalidate_existing()


def _target_month():
    t = date.today()
    return (t.year, t.month) if t.day <= 20 else ((t.year + (t.month == 12)), (t.month % 12) + 1)


def _find_day(ctx, emp_id, pair, weekdays, after):
    rule = ctx.pair_rule[pair]
    for d in ctx.dates:
        if d > after and d.weekday() in weekdays and ctx.required(rule, d) == 1 and ctx.unavailable(emp_id, d) is None:
            return d
    return None


def run_demo_scenario(user=None):
    """Scenario 1: sole employee of Brand A takes leave -> coverage gap. Scenario 2: one of a multi-employee brand's
    team takes leave -> remaining team keeps coverage. Both use the real roster, validator and impact analysis."""
    reset_demo()
    year, month = _target_month()
    if not RosterPeriod.objects.filter(year=year, month=month).exists():
        generate_roster(year, month, user)
    ctx = MonthContext(year, month)
    after = max(date.today(), ctx.first - timedelta(days=1))
    brand_a = Brand.objects.filter(brand_id="BR001").first() or next(
        (b for b in Brand.objects.all() if sum(len(v) for (bid, _), v in ctx.primary.items() if bid == b.id) == 1), None)
    multi = Brand.objects.filter(brand_id="BR020").first()
    result = {"year": year, "month": month, "leaves": []}
    plans = []
    if brand_a:
        pair = next(((brand_a.id, pid) for (bid, pid) in ctx.pair_rule if bid == brand_a.id and ctx.primary[(bid, pid)]), None)
        if pair:
            emp_id = ctx.primary[pair][0]
            d = _find_day(ctx, emp_id, pair, {1, 2, 3}, after)
            if d:
                plans.append((emp_id, d, f"{DEMO_TAG} Sole owner of {brand_a.name} requests leave"))
    if multi:
        pair = next(((multi.id, pid) for (bid, pid) in ctx.pair_rule if bid == multi.id and len(ctx.primary[(bid, pid)]) >= 2), None)
        if pair:
            emp_id = ctx.primary[pair][0]
            d = _find_day(ctx, emp_id, pair, {1, 3}, after)
            if d:
                plans.append((emp_id, d, f"{DEMO_TAG} One of {multi.name}'s team is unavailable"))
    for emp_id, d, reason in plans:
        leave = Leave.objects.create(employee_id=emp_id, start_date=d, end_date=d, leave_type="PLANNED", status="APPROVED", reason=reason)
        result["leaves"].append(leave)
    revalidate_existing()
    log_activity(user, f"Ran demo scenario for {year}-{month:02d} ({len(result['leaves'])} leaves added)")
    return result
