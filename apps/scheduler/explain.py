"""Explanations and replacement suggestions. Purely derived from the validated roster data -
they never change the roster and never invent assignments (no external AI needed)."""
from apps.core.models import RosterEntry

from .calendar_rules import MonthContext


def load_entry_map(ctx):
    entries = RosterEntry.objects.filter(date__range=(ctx.first, ctx.last)).prefetch_related("assignments")
    return {(e.employee_id, e.date): e for e in entries}


def entry_pairs(entry):
    if entry is None or entry.status != "WORKING":
        return []
    return [(a.brand_id, a.process_id) for a in entry.assignments.all()]


def team_status(ctx, emap, rule, d):
    """For every employee assigned (primary/backup) to the pair: (employee, role, state_text, is_working_on_pair)."""
    p = (rule.brand_id, rule.process_id)
    rows = []
    for role, ids in (("assigned", ctx.primary[p]), ("backup", ctx.backup[p])):
        for emp_id in ids:
            emp = ctx.employees[emp_id]
            reason = ctx.unavailable(emp_id, d)
            en = emap.get((emp_id, d))
            if reason:
                state, working = f"unavailable ({reason[1].lower()})", False
            elif p in entry_pairs(en):
                state, working = "rostered", True
            elif en is None:
                state, working = "has no roster entry", False
            elif en.status == "WEEKLY_OFF":
                state, working = "on a rotating weekly off", False
            else:
                state, working = f"available but rostered as {en.get_status_display().lower()}", False
            rows.append((emp, role, state, working))
    return rows


def candidate_replacements(ctx, emap, rule, d, limit=5):
    p = (rule.brand_id, rule.process_id)
    working_here = {e for (e, dd), en in emap.items() if dd == d and p in entry_pairs(en)}
    pod_ids = {ctx.employees[e].pod_id for e in ctx.primary[p] + ctx.backup[p] if e in ctx.employees}
    code = ctx.processes[rule.process_id].process_id.lower()
    out = []
    for emp in ctx.employees.values():
        if emp.id in working_here or ctx.unavailable(emp.id, d):
            continue
        en = emap.get((emp.id, d))
        if en is not None and en.status in ("WEEKLY_OFF", "LEAVE", "COMP_OFF"):
            continue
        cur = entry_pairs(en)
        if len(cur) >= ctx.settings.max_daily_workloads or ctx.shift_clash(cur, p):
            continue
        if emp.id in ctx.backup[p]:
            rank, why = 0, "designated backup for this brand/process"
        elif emp.id in ctx.primary[p]:
            rank, why = 0, "assigned to this brand/process but not rostered that day"
        elif any(ctx.pair_rule[q].process_id == rule.process_id for q in ctx.emp_pairs[emp.id]):
            rank, why = 1, f"already handles {ctx.processes[rule.process_id].process_id} for another brand"
        elif emp.pod_id in pod_ids:
            rank, why = 2, "same pod as the assigned team"
        elif code in emp.skills.lower():
            rank, why = 3, "has the required skill"
        else:
            rank, why = 4, "available generalist"
        busy = f"currently covers {len(cur)} workload(s)" if cur else "free that day"
        out.append({"employee": emp, "rank": rank, "why": why, "busy": busy, "workloads": len(cur)})
    out.sort(key=lambda c: (c["rank"], c["workloads"], c["employee"].employee_id))
    return out[:limit]


def describe_gap(ctx, emap, rule, d, have, need):
    label = ctx.pair_label((rule.brand_id, rule.process_id))
    day = d.strftime("%a %d %b %Y")
    head = (f"{label} needs {need} staff on {day} but has {have}." if have else
            f"{label} needs {need} staff on {day} but nobody is rostered.")
    team = team_status(ctx, emap, rule, d)
    if not team:
        reasons = " No employee is assigned to this brand/process."
    else:
        bits = [f"{e.full_name} ({role}) is {state}" for e, role, state, working in team if not working]
        reasons = (" " + "; ".join(bits) + ".") if bits else ""
    cands = candidate_replacements(ctx, emap, rule, d, limit=3)
    if cands:
        names = ", ".join(f"{c['employee'].full_name} ({c['why']}; {c['busy']})" for c in cands)
        sugg = f"Possible cover: {names}. Assign one of them manually, or add a backup resource for this brand/process."
    else:
        sugg = ("No other employee is free that day. Change someone's leave/weekly off, add a backup resource, "
                "or relax the minimum staffing rule for this day.")
    return head + reasons, sugg


def explain_conflict(conflict):
    """Plain-English explanation for the conflicts page (deterministic, rule-based)."""
    text = conflict.description
    if conflict.conflict_type in ("NO_COVERAGE", "INSUFFICIENT_STAFFING") and conflict.date and conflict.brand_id:
        ctx = MonthContext(conflict.year, conflict.month)
        rule = ctx.pair_rule.get((conflict.brand_id, conflict.process_id))
        if rule:
            emap = load_entry_map(ctx)
            team = team_status(ctx, emap, rule, conflict.date)
            lines = [f"- {e.full_name} ({role}): {state}" for e, role, state, _ in team] or ["- nobody is assigned to this pair"]
            text += "\n\nTeam on that day:\n" + "\n".join(lines)
            text += "\n\nWhy it matters: the operating rule requires this coverage, so it takes priority over weekly-off preferences."
    return text
