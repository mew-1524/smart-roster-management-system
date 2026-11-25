from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.core.models import (Brand, Conflict, Employee, Leave, Pod, Process, RosterEntry, RosterPeriod,
                              log_activity)

from . import services
from .calendar_rules import MonthContext
from .engine import generate_roster
from .exports import csv_response, xlsx_response
from .explain import candidate_replacements, explain_conflict, load_entry_map
from .selectors import MODES, RosterView, _int
from .validation import run_validation


def _ym(request, source=None):
    src = source if source is not None else request.GET
    t = date.today()
    y, m = _int(src.get("year"), t.year), _int(src.get("month"), t.month)
    if not 1 <= (m or 0) <= 12 or not 2000 <= (y or 0) <= 2100:
        y, m = t.year, t.month
    return y, m


def _picker_context(y, m):
    t = date.today()
    return {"year": y, "month": m, "years": range(t.year - 1, t.year + 3), "months": range(1, 13)}


def roster_home(request):
    rv = RosterView(request.GET)
    ctx = {"rv": rv, "mode": rv.mode, "modes": MODES, "period": RosterPeriod.objects.filter(year=rv.year, month=rv.month).first(),
           "days": rv.day_headers(), "coverage": rv.coverage_rows() if rv.roster_exists else [], "employees_all": list(rv.ctx.employees.values()),
           "pods": Pod.objects.all(), "brands": Brand.objects.filter(status="ACTIVE"), "processes": Process.objects.filter(status="ACTIVE"),
           "f": rv.f, "querystring": _clean_query(request), "open_conflicts": Conflict.objects.filter(year=rv.year, month=rv.month, status="OPEN").count(),
           **_picker_context(rv.year, rv.month)}
    if rv.mode == "employee":
        ctx["calendar"] = rv.employee_calendar()
    elif rv.mode == "pod":
        ctx["groups"] = rv.grouped_rows()
    else:
        ctx["rows"] = rv.grid_rows()
    return render(request, "scheduler/roster.html", ctx)


def _clean_query(request):
    q = request.GET.copy()
    q.pop("page", None)
    return q.urlencode()


def roster_export(request, fmt):
    if fmt not in ("csv", "xlsx"):
        raise Http404
    rv = RosterView(request.GET)
    if not rv.roster_exists:
        messages.warning(request, "There is no saved roster for that month yet. Generate one first.")
        return redirect(f"{reverse('roster_home')}?year={rv.year}&month={rv.month}")
    name = f"roster_{rv.year}-{rv.month:02d}"
    header, rows = rv.export_grid()
    if fmt == "csv":
        return csv_response(name + ".csv", header, rows)
    conflicts = Conflict.objects.filter(year=rv.year, month=rv.month).select_related("brand", "process", "employee")
    sheets = {"Roster": (header, rows), "Coverage": rv.export_coverage(),
              "Conflicts": (["ID", "Severity", "Type", "Date", "Brand", "Process", "Employee", "Status", "Description", "Suggested resolution"],
                            [[c.conflict_id, c.get_severity_display(), c.get_conflict_type_display(), c.date or "", str(c.brand or ""), str(c.process or ""),
                              str(c.employee or ""), c.get_status_display(), c.description, c.suggestion] for c in conflicts])}
    return xlsx_response(name + ".xlsx", sheets)


def entry_edit(request, emp_pk, day):
    emp = get_object_or_404(Employee, pk=emp_pk)
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise Http404
    ctx = MonthContext(d.year, d.month)
    emap = load_entry_map(ctx)
    entry = emap.get((emp.id, d))
    mine = ctx.emp_pairs.get(emp.id, []) + ctx.emp_backup_pairs.get(emp.id, [])
    options = [{"value": f"{r.brand_id}-{r.process_id}", "label": ctx.pair_label((r.brand_id, r.process_id)), "assigned": (r.brand_id, r.process_id) in mine,
                "open": ctx.required(r, d) > 0} for r in ctx.rules]
    options.sort(key=lambda o: (not o["assigned"], o["label"]))
    selected = {f"{b}-{p}" for b, p in (a for a in [(x.brand_id, x.process_id) for x in entry.assignments.all()])} if entry and entry.status == "WORKING" else set()
    state = {"status": entry.status if entry else "UNASSIGNED", "note": entry.note if entry else "", "selected": selected}
    issues = []
    if request.method == "POST":
        status = request.POST.get("status", "UNASSIGNED")
        if status not in dict(RosterEntry.STATUS):
            return HttpResponseBadRequest("Invalid status")
        chosen = set(request.POST.getlist("pairs")) if status == "WORKING" else set()
        pairs = []
        for v in chosen:
            try:
                b, p = (int(x) for x in v.split("-"))
            except ValueError:
                continue
            if (b, p) in ctx.pair_rule:
                pairs.append((b, p))
        pairs.sort()
        state = {"status": status, "note": request.POST.get("note", ""), "selected": {f"{b}-{p}" for b, p in pairs}}
        issues = services.check_proposed(ctx, emap, emp, d, status, pairs)
        if issues and not request.POST.get("confirm"):
            messages.warning(request, "This change creates conflicts. Review them below, then tick “Save anyway” to confirm.")
        else:
            result = services.save_manual_entry(request.user, emp, d, status, pairs, state["note"])
            relevant = [f for f in result["found"] if f["date"] == d and (f["employee_id"] == emp.id or f["conflict_type"] in ("NO_COVERAGE", "INSUFFICIENT_STAFFING"))]
            if relevant:
                messages.warning(request, f"Saved and re-validated: {len(relevant)} issue(s) on {d:%d %b} — " + "; ".join(f["description"] for f in relevant[:2]))
            else:
                messages.success(request, f"Saved. Validation found no issues for {emp.full_name} on {d:%d %b}.")
            return redirect(f"{reverse('roster_home')}?year={d.year}&month={d.month}")
    return render(request, "scheduler/entry_edit.html", {"emp": emp, "day": d, "state": state, "options": options, "issues": issues,
                                                         "statuses": RosterEntry.STATUS, "reason": ctx.unavailable(emp.id, d),
                                                         "has_roster": bool(emap)})


def generate_page(request):
    if request.method == "POST":
        y, m = _ym(request, request.POST)
        res = generate_roster(y, m, request.user, keep_manual=bool(request.POST.get("keep_manual")))
        c = Conflict.objects.filter(year=y, month=m)
        crit = c.filter(severity="CRITICAL").count()
        msg = (f"Roster for {y}-{m:02d} generated: {res['generated_entries']} day-entries, "
               f"{res['stats']['covered']}/{res['stats']['required']} required coverage slots fully covered, {c.count()} issue(s) flagged ({crit} critical).")
        (messages.warning if c.exists() else messages.success)(request, msg)
        return redirect(f"{reverse('conflicts')}?year={y}&month={m}")
    y, m = _ym(request)
    periods = []
    for p in RosterPeriod.objects.all():
        cs = Conflict.objects.filter(year=p.year, month=p.month)
        periods.append({"p": p, "total": cs.count(), "critical": cs.filter(severity="CRITICAL").count(),
                        "manual": RosterEntry.objects.filter(date__year=p.year, date__month=p.month, source="MANUAL").count()})
    return render(request, "scheduler/generate.html", {"periods": periods, "exists": RosterPeriod.objects.filter(year=y, month=m).exists(), **_picker_context(y, m)})


@require_POST
def validate_action(request):
    y, m = _ym(request, request.POST)
    res = run_validation(y, m)
    if not res["roster_exists"]:
        messages.warning(request, f"No roster exists for {y}-{m:02d}. Generate it first.")
    else:
        n = len(res["conflicts"])
        (messages.warning if n else messages.success)(request, f"Validation complete: {n} issue(s) found." if n else "Validation complete: no issues found.")
    log_activity(request.user, f"Validated roster {y}-{m:02d}")
    return redirect(request.POST.get("next") or f"{reverse('conflicts')}?year={y}&month={m}")


@require_POST
def publish_action(request):
    y, m = _ym(request, request.POST)
    if not RosterPeriod.objects.filter(year=y, month=m).exists():
        messages.error(request, "Generate the roster before publishing.")
    else:
        run_validation(y, m)
        blocking = Conflict.objects.filter(year=y, month=m, severity="CRITICAL", status="OPEN").count()
        if blocking:
            messages.error(request, f"Cannot publish: {blocking} critical conflict(s) are still open. Resolve or acknowledge them first.")
        else:
            services.publish_period(request.user, y, m)
            messages.success(request, f"Roster {y}-{m:02d} published.")
    return redirect(request.POST.get("next") or f"{reverse('roster_home')}?year={y}&month={m}")


def conflicts_page(request):
    y, m = _ym(request)
    qs = Conflict.objects.filter(year=y, month=m).select_related("brand", "process", "employee")
    sev, typ, st = request.GET.get("severity", ""), request.GET.get("type", ""), request.GET.get("status", "")
    brand = _int(request.GET.get("brand"))
    summary = {s: qs.filter(severity=s).count() for s, _ in Conflict.SEVERITY}
    if sev:
        qs = qs.filter(severity=sev)
    if typ:
        qs = qs.filter(conflict_type=typ)
    if st:
        qs = qs.filter(status=st)
    if brand:
        qs = qs.filter(brand_id=brand)
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    items = sorted(qs, key=lambda c: (order[c.severity], c.date or date.min, c.id))
    q = request.GET.copy()
    q.pop("page", None)
    return render(request, "scheduler/conflicts.html", {
        "page": Paginator(items, 20).get_page(request.GET.get("page")), "summary": summary, "sev": sev, "typ": typ, "st": st, "brand_f": brand,
        "types": Conflict.TYPES, "brands": Brand.objects.all(), "querystring": q.urlencode(),
        "period": RosterPeriod.objects.filter(year=y, month=m).first(), "total": sum(summary.values()), **_picker_context(y, m)})


def conflict_detail(request, pk):
    c = get_object_or_404(Conflict.objects.select_related("brand", "process", "employee"), pk=pk)
    cands, leave = [], None
    if c.conflict_type in ("NO_COVERAGE", "INSUFFICIENT_STAFFING") and c.date and c.brand_id:
        ctx = MonthContext(c.year, c.month)
        rule = ctx.pair_rule.get((c.brand_id, c.process_id))
        if rule:
            cands = candidate_replacements(ctx, load_entry_map(ctx), rule, c.date, limit=6)
    if c.conflict_type in ("LEAVE_SCHEDULED",) and c.employee_id:
        leave = Leave.objects.filter(employee_id=c.employee_id, status="APPROVED", start_date__lte=c.date, end_date__gte=c.date).first()
    return render(request, "scheduler/conflict_detail.html", {"c": c, "explanation": explain_conflict(c), "cands": cands, "leave": leave})


@require_POST
def conflict_ack(request, pk):
    c = get_object_or_404(Conflict, pk=pk)
    if c.status == "OPEN":
        c.status, c.note = "ACKNOWLEDGED", request.POST.get("note", "")[:200]
        messages.success(request, f"{c.conflict_id} acknowledged. It stays visible but no longer blocks publishing.")
    else:
        c.status, c.note = "OPEN", ""
        messages.info(request, f"{c.conflict_id} re-opened.")
    c.save()
    log_activity(request.user, f"{c.conflict_id} {c.status.lower()}")
    return redirect("conflict_detail", pk=pk)


@require_POST
def conflict_assign(request, pk):
    c = get_object_or_404(Conflict, pk=pk)
    emp = get_object_or_404(Employee, pk=request.POST.get("employee"))
    if not (c.date and c.brand_id and c.process_id):
        return HttpResponseBadRequest("This conflict has no date/brand/process to cover.")
    services.assign_cover(request.user, emp, c.date, c.brand_id, c.process_id)
    still = Conflict.objects.filter(year=c.year, month=c.month, key=c.key).first()
    if still:
        messages.warning(request, f"{emp.full_name} was assigned, but the pair is still below its minimum. Add more cover.")
        return redirect("conflict_detail", pk=still.pk)
    messages.success(request, f"{emp.full_name} assigned as cover on {c.date:%d %b}. Roster re-validated: the gap is closed.")
    return redirect(f"{reverse('conflicts')}?year={c.year}&month={c.month}")
