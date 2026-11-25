"""Reports built from the saved roster. Every report honours ?year, ?month and its filters, and exports the same rows as CSV."""
from collections import defaultdict
from datetime import date

from django.shortcuts import render
from django.urls import reverse

from apps.core.models import Conflict, Pod, RosterEntry
from apps.scheduler.exports import csv_response
from apps.scheduler.selectors import _int
from apps.scheduler.validation import run_validation

REPORTS = [
    ("employee", "Employee-wise roster", "Working days, offs, leave and workload for each employee.", "person-lines-fill"),
    ("brand", "Brand-wise coverage", "Required vs covered days for every brand and process.", "shop"),
    ("pod", "Pod-wise staffing", "Headcount, working days and absences for each pod.", "grid-3x3-gap"),
    ("conflicts", "Conflict report", "Every open or acknowledged conflict with its suggested resolution.", "exclamation-triangle"),
]


def _ym(request):
    t = date.today()
    y, m = _int(request.GET.get("year"), t.year), _int(request.GET.get("month"), t.month)
    if not 1 <= m <= 12:
        y, m = t.year, t.month
    return y, m


def index(request):
    y, m = _ym(request)
    return render(request, "reports/index.html", {"reports": REPORTS, "year": y, "month": m})


def _respond(request, key, title, header, rows, y, m, extra=None):
    if request.GET.get("format") == "csv":
        return csv_response(f"{key}_report_{y}-{m:02d}.csv", header, rows)
    t = date.today()
    ctx = {"key": key, "title": title, "header": header, "rows": rows, "year": y, "month": m, "years": range(t.year - 1, t.year + 3),
           "months": range(1, 13), "querystring": request.GET.urlencode(), "pods": Pod.objects.all(), "empty": not rows}
    ctx.update(extra or {})
    return render(request, "reports/report.html", ctx)


def employee_report(request):
    y, m = _ym(request)
    pod = _int(request.GET.get("pod"))
    entries = RosterEntry.objects.filter(date__year=y, date__month=m).select_related("employee", "employee__pod").prefetch_related("assignments")
    if pod:
        entries = entries.filter(employee__pod_id=pod)
    data = defaultdict(lambda: defaultdict(int))
    emps = {}
    for e in entries:
        emps[e.employee_id] = e.employee
        data[e.employee_id][e.status] += 1
        data[e.employee_id]["workloads"] += len(e.assignments.all())
    header = ["Employee ID", "Name", "Pod", "Weekly off", "Working", "Weekly off days", "Leave", "Comp-off", "Public holiday", "Unassigned", "Total workloads"]
    rows = [[e.employee_id, e.full_name, e.pod.name if e.pod else "", e.weekly_off_display, d["WORKING"], d["WEEKLY_OFF"], d["LEAVE"], d["COMP_OFF"],
             d["HOLIDAY"], d["UNASSIGNED"], d["workloads"]] for e, d in sorted(((emps[k], data[k]) for k in emps), key=lambda x: x[0].employee_id)]
    return _respond(request, "employee", "Employee-wise roster report", header, rows, y, m, {"filter_pod": True, "pod_value": pod})


def brand_report(request):
    y, m = _ym(request)
    res = run_validation(y, m, persist=False)
    ctx = res["ctx"]
    brand = _int(request.GET.get("brand"))
    rows = []
    for (b, p), (req, cov, ins, gap) in sorted(res["stats"]["by_pair"].items(), key=lambda kv: (ctx.brands[kv[0][0]].brand_id, ctx.processes[kv[0][1]].process_id)):
        if brand and b != brand:
            continue
        rule = ctx.pair_rule[(b, p)]
        team = len(ctx.primary[(b, p)])
        rows.append([ctx.brands[b].brand_id, ctx.brands[b].name, ctx.processes[p].process_id, rule.calendar_display, rule.min_staff, team, req, cov, ins, gap,
                     f"{round(100 * cov / req)}%" if req else "—"])
    header = ["Brand ID", "Brand", "Process", "Operating calendar", "Min staff", "Team size", "Required days", "Fully covered", "Insufficient", "No coverage", "Coverage %"]
    from apps.core.models import Brand
    return _respond(request, "brand", "Brand-wise coverage report", header, rows, y, m, {"filter_brand": True, "brands": Brand.objects.all(), "brand_value": brand,
                                                                                         "note": None if res["roster_exists"] else "No roster has been generated for this month."})


def pod_report(request):
    y, m = _ym(request)
    entries = RosterEntry.objects.filter(date__year=y, date__month=m).select_related("employee__pod")
    days = max(1, len({e.date for e in entries}))
    agg = defaultdict(lambda: defaultdict(int))
    for e in entries:
        agg[e.employee.pod_id][e.status] += 1
    rows = []
    for pod in Pod.objects.select_related("lead"):
        a = agg[pod.id]
        size = pod.employees.filter(is_active=True).count()
        rows.append([pod.name, pod.lead.full_name if pod.lead else "—", size, a["WORKING"], round(a["WORKING"] / days, 1), a["WEEKLY_OFF"], a["LEAVE"], a["COMP_OFF"], a["HOLIDAY"], a["UNASSIGNED"]])
    header = ["Pod", "Pod lead", "Employees", "Working person-days", "Avg staffed / day", "Weekly offs", "Leave days", "Comp-offs", "Holidays", "Unassigned"]
    return _respond(request, "pod", "Pod-wise staffing report", header, rows, y, m)


def conflict_report(request):
    y, m = _ym(request)
    sev = request.GET.get("severity", "")
    qs = Conflict.objects.filter(year=y, month=m).select_related("brand", "process", "employee")
    if sev:
        qs = qs.filter(severity=sev)
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    rows = [[c.conflict_id, c.get_severity_display(), c.get_conflict_type_display(), c.date or "", c.brand or "", c.process.process_id if c.process else "",
             c.employee.full_name if c.employee else "", c.get_status_display(), c.description, c.suggestion]
            for c in sorted(qs, key=lambda c: (order[c.severity], c.date or date.min, c.id))]
    header = ["Conflict ID", "Severity", "Type", "Date", "Brand", "Process", "Employee", "Status", "Description", "Suggested resolution"]
    return _respond(request, "conflicts", "Conflict report", header, rows, y, m, {"filter_severity": True, "sev_value": sev, "severities": Conflict.SEVERITY})
