"""Deterministic, constraint-based roster generator (no LLM, no randomness).

Algorithm (all mandatory constraints are hard filters; preferences only break ties):
  1. Load the month (MonthContext): operating rules, assignments, holidays, approved leave/comp-offs.
  2. Choose weekly offs for ROTATING employees, week by week, picking the day that (a) never reduces
     coverage below the minimum if avoidable, (b) honours the employee's preferred off day, (c) is the
     quietest day, (d) spreads offs across days and across weeks (fair distribution).
  3. Day by day, every available employee is rostered on each of his/her assigned brand-process pairs
     that operate that day (subject to the per-day workload limit and shift compatibility).
  4. Gap fill: where coverage is still below the minimum, designated backup resources who are free
     (not off/leave/comp-off, under the workload limit, no shift clash) are added as "cover".
  5. Anything still uncovered is NOT hidden - the validator reports it as a conflict.
Manual entries are preserved on regeneration (keep_manual) to avoid unnecessary schedule churn.
"""
from collections import defaultdict
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.core.models import RosterAssignment, RosterEntry, RosterPeriod, log_activity

from .calendar_rules import MonthContext


def plan_rotating_offs(ctx, manual):
    rotating = [e for e in ctx.employees.values() if e.off_mode == "ROTATING"]
    off = defaultdict(set)
    if not rotating:
        return off
    hist = defaultdict(lambda: defaultdict(int))
    max_run = ctx.settings.max_consecutive_days
    prev = {e.id: ctx.first - timedelta(days=1) for e in rotating}  # last rest day, to cap consecutive work days
    # tightest employees (fewest peers) choose first
    rotating.sort(key=lambda e: (sum(len(ctx.primary[p]) for p in ctx.emp_pairs[e.id]), e.employee_id))
    weeks = defaultdict(list)
    for d in ctx.dates:
        weeks[d - timedelta(days=d.weekday())].append(d)
    for monday in sorted(weeks):
        days = weeks[monday]
        for emp in rotating:
            rest = [d for d in days if (emp.id, d) in ctx.blocked or (emp.id, d) in manual]
            if rest and (min(rest) - prev[emp.id]).days <= max_run + 1:
                prev[emp.id] = max(rest)
                continue  # leave / comp-off / manual edit already provides this week's rest
            cands = [d for d in days if d not in rest]
            if not cands:
                continue

            if len(days) < 4 and not rest and (days[-1] - prev[emp.id]).days <= max_run:
                continue  # short week at the month edge: only force a rest day if the work streak would get too long

            def score(d, emp=emp):
                deficit = ops = 0
                for p in ctx.emp_pairs[emp.id]:
                    need = ctx.required(ctx.pair_rule[p], d)
                    if need == 0:
                        continue
                    ops += 1
                    avail = sum(1 for o in ctx.primary[p] if o != emp.id and ctx.unavailable(o, d) is None and o not in off[d])
                    deficit += max(0, need - avail)
                too_long = 1 if (d - prev[emp.id]).days > max_run + 1 else 0
                pref = 0 if d.weekday() in emp.preferred_set else 1
                return (deficit, too_long, pref, ops, len(off[d]), hist[emp.id][d.weekday()], d)

            best = min(cands, key=score)
            off[best].add(emp.id)
            prev[emp.id] = best
            hist[emp.id][best.weekday()] += 1
    return off


@transaction.atomic
def generate_roster(year, month, user=None, keep_manual=True):
    ctx = MonthContext(year, month)
    qs = RosterEntry.objects.filter(date__range=(ctx.first, ctx.last))
    manual = {}
    if keep_manual:
        for e in qs.filter(source="MANUAL").prefetch_related("assignments"):
            manual[(e.employee_id, e.date)] = e
    qs.exclude(pk__in=[e.pk for e in manual.values()]).delete()

    off_plan = plan_rotating_offs(ctx, manual)
    max_load = ctx.settings.max_daily_workloads
    plan, counts, load = {}, defaultdict(int), defaultdict(int)

    # --- phase 1: primary team --------------------------------------------------
    for d in ctx.dates:
        for emp_id in ctx.employees:
            key = (emp_id, d)
            if key in manual:
                m = manual[key]
                if m.status == "WORKING":
                    load[emp_id] += 1
                    if ctx.unavailable(emp_id, d) is None:
                        for a in m.assignments.all():
                            counts[((a.brand_id, a.process_id), d)] += 1
                continue
            reason = ctx.unavailable(emp_id, d)
            if reason:
                plan[key] = {"status": reason[0], "pairs": [], "note": reason[1]}
                continue
            if emp_id in off_plan.get(d, ()):
                plan[key] = {"status": "WEEKLY_OFF", "pairs": [], "note": "Rotating weekly off"}
                continue
            pairs = []
            for p in sorted(ctx.emp_pairs[emp_id]):
                if ctx.required(ctx.pair_rule[p], d) == 0 or len(pairs) >= max_load:
                    continue
                if ctx.shift_clash([q for q, _ in pairs], p):
                    continue
                pairs.append((p, False))
                counts[(p, d)] += 1
            if pairs:
                load[emp_id] += 1
            plan[key] = {"status": "WORKING" if pairs else None, "pairs": pairs, "note": ""}

    # --- phase 2: backup resources fill remaining gaps --------------------------
    for d in ctx.dates:
        for rule in ctx.rules:
            p = (rule.brand_id, rule.process_id)
            need = ctx.required(rule, d) - counts[(p, d)]
            if need <= 0:
                continue
            for emp_id in sorted(ctx.backup[p], key=lambda e: (load[e], e)):
                if need <= 0:
                    break
                pl = plan.get((emp_id, d))
                if pl is None or pl["status"] not in (None, "WORKING"):
                    continue
                pairs = pl["pairs"]
                if any(q == p for q, _ in pairs) or len(pairs) >= max_load or ctx.shift_clash([q for q, _ in pairs], p):
                    continue
                if not pairs:
                    load[emp_id] += 1
                pairs.append((p, True))
                pl["status"], pl["note"] = "WORKING", "Backup cover"
                counts[(p, d)] += 1
                need -= 1

    # --- persist ------------------------------------------------------------------
    entries = []
    for (emp_id, d), pl in plan.items():
        status = pl["status"] or ("HOLIDAY" if ctx.holiday_for_employee(emp_id, d) else "UNASSIGNED")
        shift = next((ctx.pair_rule[p].shift_label for p, _ in pl["pairs"] if ctx.pair_rule[p].shift_label), "")
        entries.append(RosterEntry(employee_id=emp_id, date=d, status=status, shift=shift, source="GENERATED", note=pl["note"]))
    RosterEntry.objects.bulk_create(entries, batch_size=500)
    ids = {(e.employee_id, e.date): e.id for e in RosterEntry.objects.filter(date__range=(ctx.first, ctx.last), source="GENERATED")}
    assignments = [RosterAssignment(entry_id=ids[key], brand_id=p[0], process_id=p[1], is_cover=cover)
                   for key, pl in plan.items() for p, cover in pl["pairs"]]
    RosterAssignment.objects.bulk_create(assignments, batch_size=500)

    RosterPeriod.objects.update_or_create(year=year, month=month, defaults={"status": "DRAFT", "generated_at": timezone.now(), "published_at": None})
    log_activity(user, f"Generated roster for {year}-{month:02d} ({len(entries)} day-entries, {len(manual)} manual kept)")
    from .validation import run_validation
    result = run_validation(year, month)
    result["generated_entries"] = len(entries)
    result["manual_kept"] = len(manual)
    return result
