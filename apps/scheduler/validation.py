"""Roster validation. Produces Conflict rows (persisted) with severity, description and suggested resolution.

Mandatory constraints (invalid assignments never count as coverage):
  approved leave / comp-off, fixed weekly off, missing brand-process assignment, closed day, inactive employee.
Preferences / soft rules (reported at lower severity): consecutive days, preferred weekly off.
"""
from collections import defaultdict

from django.db import transaction

from apps.core.models import Conflict, RosterEntry

from .calendar_rules import MonthContext
from .explain import describe_gap, entry_pairs, load_entry_map

SEV_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


@transaction.atomic
def run_validation(year, month, persist=True):
    ctx = MonthContext(year, month)
    emap = load_entry_map(ctx)
    stats = {"required": 0, "covered": 0, "insufficient": 0, "gap": 0, "per_day_working": {}, "by_pair": {}}
    if not emap:
        if persist:
            Conflict.objects.filter(year=year, month=month).delete()
        return {"roster_exists": False, "conflicts": [], "stats": stats, "ctx": ctx}

    found = {}

    def add(ctype, sev, d, brand, process, emp, desc, sugg=""):
        key = f"{ctype}|{d}|{brand}|{process}|{emp}"
        found.setdefault(key, dict(conflict_type=ctype, severity=sev, date=d, brand_id=brand, process_id=process,
                                   employee_id=emp, description=desc, suggestion=sugg, key=key))

    # -- configuration level -------------------------------------------------------
    for rule in ctx.rules:
        p = (rule.brand_id, rule.process_id)
        needs = [ctx.required(rule, d) for d in ctx.dates]
        if not any(needs):
            continue
        label = ctx.pair_label(p)
        if not ctx.primary[p] and not ctx.backup[p]:
            add("NO_ASSIGNMENT", "CRITICAL", None, rule.brand_id, rule.process_id, None,
                f"{label} requires coverage this month but no active employee is assigned to it.",
                "Assign at least one employee to this brand/process (Assignments page).")
        elif max(needs) > len(set(ctx.primary[p] + ctx.backup[p])):
            add("CONFIG_INFEASIBLE", "MEDIUM", None, rule.brand_id, rule.process_id, None,
                f"{label} needs up to {max(needs)} staff on some days but only {len(set(ctx.primary[p] + ctx.backup[p]))} employee(s) are assigned.",
                "Assign more employees or lower the minimum staffing rule.")

    # -- entry level ---------------------------------------------------------------
    covered = defaultdict(set)
    working_per_day = defaultdict(int)
    for (emp_id, d), en in emap.items():
        if en.status != "WORKING":
            continue
        working_per_day[d] += 1
        emp = ctx.employees.get(emp_id)
        if emp is None:
            add("INACTIVE_EMPLOYEE", "HIGH", d, None, None, emp_id, f"An inactive employee is rostered on {d:%d %b}.",
                "Remove the assignment or reactivate the employee.")
            continue
        pairs = entry_pairs(en)
        reason = ctx.unavailable(emp_id, d)
        if reason:
            ctype, sev = {"LEAVE": ("LEAVE_SCHEDULED", "HIGH"), "COMP_OFF": ("COMPOFF_SCHEDULED", "HIGH"),
                          "WEEKLY_OFF": ("WEEKLY_OFF_SCHEDULED", "HIGH")}.get(reason[0], ("NOT_ASSIGNED", "MEDIUM"))
            what = {"LEAVE_SCHEDULED": "approved leave", "COMPOFF_SCHEDULED": "an approved comp-off",
                    "WEEKLY_OFF_SCHEDULED": "their weekly off"}.get(ctype, "a period of unavailability")
            add(ctype, sev, d, None, None, emp_id,
                f"{emp.full_name} is rostered to work on {d:%a %d %b} but is on {what}. This assignment is invalid and does not count towards coverage.",
                "Update the roster entry to the correct status (open the conflict and use 'Apply leave to roster') and assign cover if needed.")
            continue
        for p in pairs:
            if p not in ctx.emp_pairs[emp_id] and p not in ctx.emp_backup_pairs[emp_id]:
                add("NOT_ASSIGNED", "HIGH", d, p[0], p[1], emp_id,
                    f"{emp.full_name} is rostered on {ctx.pair_label(p) if p in ctx.pair_rule else p} but is not assigned to it.",
                    "Add an assignment for this employee or remove the roster entry.")
                continue
            rule = ctx.pair_rule.get(p)
            if rule is None:
                continue
            if ctx.required(rule, d) == 0:
                add("NON_OPERATING", "MEDIUM", d, p[0], p[1], emp_id,
                    f"{emp.full_name} is rostered on {ctx.pair_label(p)} on {d:%a %d %b}, a day this process is closed.",
                    "Remove the assignment or change the operating rule.")
                continue
            covered[(p, d)].add(emp_id)
        if len(pairs) > ctx.settings.max_daily_workloads:
            add("OVERLOAD", "MEDIUM", d, None, None, emp_id,
                f"{emp.full_name} covers {len(pairs)} brand-process workloads on {d:%a %d %b} (limit {ctx.settings.max_daily_workloads}).",
                "Move one workload to another employee.")
        for i, p in enumerate(pairs):
            if p in ctx.pair_rule and ctx.shift_clash(pairs[:i], p):
                add("SHIFT_CLASH", "HIGH", d, p[0], p[1], emp_id,
                    f"{emp.full_name} has incompatible shifts on {d:%a %d %b} ({ctx.pair_label(p)}).",
                    "Split these workloads between different employees.")

    # -- coverage ------------------------------------------------------------------
    for rule in ctx.rules:
        p = (rule.brand_id, rule.process_id)
        for d in ctx.dates:
            need = ctx.required(rule, d)
            if not need:
                continue
            have = len(covered[(p, d)])
            stats["required"] += 1
            bp = stats["by_pair"].setdefault(p, [0, 0, 0, 0])  # required, covered, insufficient, gap
            bp[0] += 1
            if have >= need:
                stats["covered"] += 1
                bp[1] += 1
                continue
            desc, sugg = describe_gap(ctx, emap, rule, d, have, need)
            if have == 0:
                stats["gap"] += 1
                bp[3] += 1
                add("NO_COVERAGE", "CRITICAL", d, rule.brand_id, rule.process_id, None, desc, sugg)
            else:
                stats["insufficient"] += 1
                bp[2] += 1
                add("INSUFFICIENT_STAFFING", "HIGH", d, rule.brand_id, rule.process_id, None, desc, sugg)

    # -- soft rules / preferences -------------------------------------------------------
    max_run = ctx.settings.max_consecutive_days
    for emp in ctx.employees.values():
        run = 0
        for d in ctx.dates:
            en = emap.get((emp.id, d))
            if en is not None and en.status == "WORKING":
                run += 1
                if run == max_run + 1:
                    add("MAX_CONSECUTIVE", "MEDIUM", d, None, None, emp.id,
                        f"{emp.full_name} works more than {max_run} days in a row (reaching {d:%d %b}).",
                        "Give a rest day (weekly off, comp-off) before this date.")
            else:
                run = 0
        if emp.off_mode == "ROTATING" and emp.preferred_set:
            by_week = defaultdict(list)
            for d in ctx.dates:
                en = emap.get((emp.id, d))
                if en is not None and en.status == "WEEKLY_OFF":
                    by_week[d.isocalendar()[:2]].append(d)
            for week, days in by_week.items():
                if not any(x.weekday() in emp.preferred_set for x in days):
                    add("PREFERENCE", "LOW", days[0], None, None, emp.id,
                        f"{emp.full_name}'s weekly off on {days[0]:%a %d %b} is not a preferred day (coverage or rest-day rules took priority).",
                        "No action needed unless the preference can be accommodated.")

    stats["per_day_working"] = {d.isoformat(): working_per_day.get(d, 0) for d in ctx.dates}
    conflicts = _persist(year, month, found) if persist else None
    return {"roster_exists": True, "conflicts": conflicts, "found": list(found.values()), "stats": stats, "ctx": ctx}


def _persist(year, month, found):
    existing = {c.key: c for c in Conflict.objects.filter(year=year, month=month)}
    Conflict.objects.filter(year=year, month=month).exclude(key__in=found.keys()).delete()
    for key, data in found.items():
        c = existing.get(key)
        if c:
            for k in ("severity", "description", "suggestion"):
                setattr(c, k, data[k])
            c.save(update_fields=["severity", "description", "suggestion"])
        else:
            Conflict.objects.create(year=year, month=month, **data)
    return sorted(Conflict.objects.filter(year=year, month=month).select_related("brand", "process", "employee"),
                  key=lambda c: (SEV_ORDER[c.severity], c.date or c.created_at.date(), c.id))
