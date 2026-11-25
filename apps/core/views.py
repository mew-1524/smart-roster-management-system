from collections import Counter
from datetime import date

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.scheduler import services
from apps.scheduler.validation import run_validation

from . import forms, models
from .models import ActivityLog, Brand, CompOff, Conflict, Employee, Holiday, Leave, Pod, Process, RosterEntry, RosterPeriod, log_activity


def month_from(request):
    today = date.today()
    try:
        y, m = int(request.GET.get("year", today.year)), int(request.GET.get("month", today.month))
        if not (1 <= m <= 12 and 2000 <= y <= 2100):
            raise ValueError
    except ValueError:
        y, m = today.year, today.month
    return y, m


def dashboard(request):
    y, m = month_from(request)
    res = run_validation(y, m, persist=False)
    stats = res["stats"]
    required = stats["required"]
    scheduled = RosterEntry.objects.filter(date__year=y, date__month=m, status="WORKING").values("employee").distinct().count()
    cards = [
        ("Total Employees", Employee.objects.filter(is_active=True).count(), "people", "primary"),
        ("Active Brands", Brand.objects.filter(status="ACTIVE").count(), "shop", "info"),
        ("Active Processes", Process.objects.filter(status="ACTIVE").count(), "diagram-3", "info"),
        ("Total Pods", Pod.objects.count(), "grid-3x3-gap", "primary"),
        ("Scheduled Employees", scheduled, "calendar-check", "success"),
        ("Coverage", f"{round(100 * stats['covered'] / required)}%" if required else "n/a", "shield-check", "success"),
        ("Pending Leaves", Leave.objects.filter(status="PENDING").count(), "hourglass-split", "warning"),
        ("Active Conflicts", Conflict.objects.filter(year=y, month=m, status="OPEN").count(), "exclamation-octagon", "danger"),
    ]
    brand_counts = (Brand.objects.filter(status="ACTIVE").annotate(n=Count("assignments", filter=Q(assignments__is_active=True, assignments__is_backup=False), distinct=True))
                    .order_by("-n", "name")[:15])
    leave_qs = Leave.objects.filter(start_date__lte=date(y, m, 28), end_date__gte=date(y, m, 1))
    leave_dist = Counter(l.get_leave_type_display() for l in leave_qs)
    charts = {
        "pods": {"labels": [p.name for p in Pod.objects.all()],
                 "data": [p.employees.filter(is_active=True).count() for p in Pod.objects.all()]},
        "brands": {"labels": [b.name for b in brand_counts],
                   "data": [Assignment_count(b) for b in brand_counts]},
        "days": {"labels": [k[8:] for k in stats["per_day_working"]], "data": list(stats["per_day_working"].values())},
        "coverage": {"labels": ["Fully covered", "Insufficient staffing", "No coverage"],
                     "data": [stats["covered"], stats["insufficient"], stats["gap"]]},
        "leaves": {"labels": list(leave_dist.keys()), "data": list(leave_dist.values())},
    }
    return render(request, "dashboard.html", {"cards": cards, "charts": charts, "year": y, "month": m, "roster_exists": res["roster_exists"],
                                              "activity": ActivityLog.objects.select_related("user")[:8], "period": RosterPeriod.objects.filter(year=y, month=m).first(),
                                              "years": range(date.today().year - 1, date.today().year + 3), "months": range(1, 13)})


def Assignment_count(b):
    return b.n


# ------------------------------------------------------------------ availability
LEAVE_BADGE = {"APPROVED": "success", "PENDING": "warning", "REJECTED": "danger"}


def availability(request):
    tab = request.GET.get("tab", "leaves")
    status = request.GET.get("status", "")
    ctx = {"tab": tab, "status": status, "badge": LEAVE_BADGE}
    if tab == "compoffs":
        qs = CompOff.objects.select_related("employee")
        if status:
            qs = qs.filter(status=status)
        ctx["page"] = Paginator(qs, 15).get_page(request.GET.get("page"))
    elif tab == "weekly":
        q = request.GET.get("q", "")
        qs = Employee.objects.filter(is_active=True).select_related("pod")
        if q:
            qs = qs.filter(Q(full_name__icontains=q) | Q(employee_id__icontains=q))
        ctx["page"], ctx["q"] = Paginator(qs, 15).get_page(request.GET.get("page")), q
    else:
        qs = Leave.objects.select_related("employee")
        if status:
            qs = qs.filter(status=status)
        ctx["page"] = Paginator(qs, 15).get_page(request.GET.get("page"))
    return render(request, "availability/index.html", ctx)


def _revalidate_dates(*dates):
    for y, mo in {(d.year, d.month) for d in dates if d}:
        if RosterPeriod.objects.filter(year=y, month=mo).exists():
            run_validation(y, mo)


def leave_form(request, pk=None):
    obj = get_object_or_404(Leave, pk=pk) if pk else None
    form = forms.LeaveForm(request.POST or None, instance=obj, initial={"status": "APPROVED"} if not obj else None)
    if request.method == "POST" and form.is_valid():
        leave = form.save()
        for y, mo in services.months_between(leave.start_date, leave.end_date):
            if RosterPeriod.objects.filter(year=y, month=mo).exists():
                run_validation(y, mo)
        log_activity(request.user, f"Leave {leave.get_status_display().lower()}: {leave.employee.employee_id} {leave.start_date}→{leave.end_date}")
        messages.success(request, "Leave saved and the roster was re-validated.")
        return redirect("leave_impact", pk=leave.pk)
    return render(request, "availability/form.html", {"form": form, "title": "Edit leave" if obj else "Apply leave", "tab": "leaves"})


@require_POST
def leave_action(request, pk, action):
    leave = get_object_or_404(Leave, pk=pk)
    if action == "delete":
        rng = (leave.start_date, leave.end_date)
        leave.delete()
        for y, mo in services.months_between(*rng):
            if RosterPeriod.objects.filter(year=y, month=mo).exists():
                run_validation(y, mo)
        messages.success(request, "Leave deleted.")
        return redirect("/availability/?tab=leaves")
    leave.status = "APPROVED" if action == "approve" else "REJECTED"
    leave.save()
    for y, mo in services.months_between(leave.start_date, leave.end_date):
        if RosterPeriod.objects.filter(year=y, month=mo).exists():
            run_validation(y, mo)
    log_activity(request.user, f"Leave {leave.status.lower()}: {leave.employee.employee_id} {leave.start_date}→{leave.end_date}")
    messages.success(request, f"Leave {leave.status.lower()}. The roster was re-validated.")
    return redirect("leave_impact", pk=leave.pk)


def leave_impact(request, pk):
    leave = get_object_or_404(Leave.objects.select_related("employee"), pk=pk)
    rows, notes = services.leave_impact(leave)
    conflicts = Conflict.objects.filter(date__range=(leave.start_date, leave.end_date), employee=leave.employee) | \
        Conflict.objects.filter(date__range=(leave.start_date, leave.end_date), conflict_type__in=["NO_COVERAGE", "INSUFFICIENT_STAFFING"])
    return render(request, "scheduler/impact.html", {"leave": leave, "rows": rows, "notes": notes,
                                                     "conflicts": conflicts.select_related("brand", "process", "employee").distinct()})


@require_POST
def leave_apply(request, pk):
    leave = get_object_or_404(Leave, pk=pk)
    if leave.status != "APPROVED":
        messages.error(request, "Only approved leave can be applied to the roster.")
    else:
        n = services.apply_leave_to_roster(request.user, leave)
        messages.success(request, f"Roster updated: {n} working day(s) changed to Leave. Any remaining coverage gap is still flagged.")
    return redirect("leave_impact", pk=pk)


def compoff_form(request, pk=None):
    obj = get_object_or_404(CompOff, pk=pk) if pk else None
    form = forms.CompOffForm(request.POST or None, instance=obj, initial={"status": "APPROVED"} if not obj else None)
    if request.method == "POST" and form.is_valid():
        c = form.save()
        _revalidate_dates(c.date)
        log_activity(request.user, f"Comp-off {c.get_status_display().lower()}: {c.employee.employee_id} on {c.date}")
        messages.success(request, "Comp-off saved and the roster was re-validated.")
        return redirect("/availability/?tab=compoffs")
    return render(request, "availability/form.html", {"form": form, "title": "Edit comp-off" if obj else "Add comp-off", "tab": "compoffs"})


@require_POST
def compoff_action(request, pk, action):
    c = get_object_or_404(CompOff, pk=pk)
    d = c.date
    if action == "delete":
        c.delete()
    else:
        c.status = "APPROVED" if action == "approve" else "REJECTED"
        c.save()
    _revalidate_dates(d)
    messages.success(request, "Comp-off updated and the roster was re-validated.")
    return redirect("/availability/?tab=compoffs")


def weekly_off_edit(request, pk):
    emp = get_object_or_404(Employee, pk=pk)
    form = forms.WeeklyOffForm(request.POST or None, instance=emp)
    if request.method == "POST" and form.is_valid():
        form.save()
        services.revalidate_existing()
        log_activity(request.user, f"Weekly off updated for {emp.employee_id}")
        messages.success(request, f"Weekly-off pattern saved for {emp.full_name}. Regenerate the roster to apply it to new days.")
        return redirect("/availability/?tab=weekly")
    return render(request, "availability/form.html", {"form": form, "title": f"Weekly offs · {emp.full_name}", "tab": "weekly"})


# ------------------------------------------------------------------ settings / search
def settings_view(request):
    obj = models.RosterSettings.get()
    form = forms.SettingsForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        services.revalidate_existing()
        messages.success(request, "Settings saved.")
        return redirect("settings")
    counts = [("Employees", Employee.objects.count()), ("Brands", Brand.objects.count()), ("Processes", Process.objects.count()),
              ("Operating rules", models.OperatingRule.objects.count()), ("Assignments", models.Assignment.objects.count()),
              ("Roster entries", RosterEntry.objects.count())]
    return render(request, "settings.html", {"form": form, "counts": counts,
                                             "demo_leaves": Leave.objects.filter(reason__startswith=services.DEMO_TAG).select_related("employee")})


@require_POST
def demo_run(request):
    res = services.run_demo_scenario(request.user)
    if not res["leaves"]:
        messages.warning(request, "Demo scenario needs the sample data. Run: python manage.py seed_data")
        return redirect("settings")
    messages.success(request, "Demo scenario applied: two approved leaves were added and the roster was re-validated.")
    return redirect("leave_impact", pk=res["leaves"][0].pk)


@require_POST
def demo_reset(request):
    services.reset_demo()
    messages.success(request, "Demo leaves removed and the roster re-validated.")
    return redirect("settings")


def search(request):
    q = request.GET.get("q", "").strip()
    ctx = {"q": q}
    if q:
        ctx["employees"] = Employee.objects.filter(Q(full_name__icontains=q) | Q(employee_id__icontains=q) | Q(email__icontains=q))[:20]
        ctx["brands"] = Brand.objects.filter(Q(name__icontains=q) | Q(brand_id__icontains=q))[:20]
        ctx["processes"] = Process.objects.filter(Q(name__icontains=q) | Q(process_id__icontains=q))[:20]
    return render(request, "search.html", ctx)
