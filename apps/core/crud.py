"""Config-driven CRUD: one generic set of views serves Employees, Brands, Processes, Pods, Assignments,
Operating Rules and Public Holidays. Every list has search, filters and pagination; every action is wired."""
from dataclasses import dataclass, field
from typing import Callable

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path, reverse
from django.views.decorators.http import require_POST

from apps.scheduler.services import revalidate_existing

from . import forms, models
from .models import log_activity


def link(text, slug, obj, name="detail"):
    return ("link", text, reverse(f"{slug}_{name}", args=[obj.pk]))


def badge(text, cls="secondary"):
    return ("badge", text, cls)


def active_badge(flag):
    return badge("Active" if flag else "Inactive", "success" if flag else "secondary")


@dataclass
class Cfg:
    slug: str
    model: type
    form: type
    title: str
    plural: str
    icon: str
    columns: list
    search: list = field(default_factory=list)
    filters: list = field(default_factory=list)  # (param, label, lookup, choices_callable)
    ordering: tuple = ()
    detail: Callable = None  # obj -> (list of (label, value), list of table sections)
    toggle: str = ""  # field toggled by activate/deactivate ('is_active' or 'status')
    deletable: bool = True
    affects_roster: bool = True
    select_related: tuple = ()


def _yesno(v):
    return "Yes" if v else "No"


def employee_detail(o):
    fields = [("Employee ID", o.employee_id), ("Email", o.email or "—"), ("Phone", o.phone or "—"),
              ("Pod", o.pod or "—"), ("Designation", o.designation or "—"), ("Joining date", o.joining_date or "—"),
              ("Employment", o.get_employment_status_display()), ("Skills", o.skills or "—"),
              ("Weekly off", o.weekly_off_display), ("Preferred off days", models.format_days(o.preferred_off_days)),
              ("Status", "Active" if o.is_active else "Inactive")]
    rows = [[link(f"{a.brand.name}", "brands", a.brand), a.process.name, "Backup" if a.is_backup else "Primary",
             "Active" if a.is_active else "Inactive"] for a in o.assignments.select_related("brand", "process")]
    leaves = [[l.start_date, l.end_date, l.get_leave_type_display(), badge(l.get_status_display(), {"APPROVED": "success", "PENDING": "warning", "REJECTED": "danger"}[l.status])]
              for l in o.leaves.all()[:8]]
    return fields, [{"title": "Assignments", "headers": ["Brand", "Process", "Role", "Status"], "rows": rows},
                    {"title": "Recent leave", "headers": ["From", "To", "Type", "Status"], "rows": leaves},
                    {"title": "Schedule", "headers": [], "rows": [[("link", "Open this employee's monthly schedule", reverse("roster_home") + f"?view=employee&employee={o.pk}")]]}]


def brand_detail(o):
    fields = [("Brand ID", o.brand_id), ("Description", o.description or "—"), ("Status", o.get_status_display())]
    rules = [[r.process.name, r.calendar_display, r.min_staff, r.weekday_min_staff or "—", link("Edit", "rules", r, "edit")]
             for r in o.rules.select_related("process")]
    team = [[link(a.employee.full_name, "employees", a.employee), a.process.name, "Backup" if a.is_backup else "Primary"]
            for a in o.assignments.filter(is_active=True).select_related("employee", "process")]
    return fields, [{"title": "Operating rules (per process)", "headers": ["Process", "Calendar", "Min staff", "Overrides", ""], "rows": rules},
                    {"title": "Team", "headers": ["Employee", "Process", "Role"], "rows": team}]


def process_detail(o):
    fields = [("Process ID", o.process_id), ("Description", o.description or "—"), ("Status", o.get_status_display())]
    rules = [[link(r.brand.name, "brands", r.brand), r.calendar_display, r.min_staff, link("Edit", "rules", r, "edit")]
             for r in o.rules.select_related("brand")]
    return fields, [{"title": "Brands using this process", "headers": ["Brand", "Calendar", "Min staff", ""], "rows": rules}]


def pod_detail(o):
    fields = [("Pod ID", o.pod_id), ("Pod lead", o.lead or "—"), ("Description", o.description or "—")]
    rows = [[link(e.full_name, "employees", e), e.designation, e.weekly_off_display] for e in o.employees.filter(is_active=True)]
    return fields, [{"title": "Members", "headers": ["Employee", "Designation", "Weekly off"], "rows": rows}]


def simple_detail(pairs):
    return lambda o: ([(label, fn(o)) for label, fn in pairs], [])


CONFIGS = [
    Cfg("employees", models.Employee, forms.EmployeeForm, "Employee", "Employees", "people",
        [("ID", lambda o: link(o.employee_id, "employees", o)), ("Name", lambda o: o.full_name), ("Pod", lambda o: str(o.pod or "—")),
         ("Designation", lambda o: o.designation), ("Weekly off", lambda o: o.weekly_off_display), ("Status", lambda o: active_badge(o.is_active))],
        search=["employee_id", "full_name", "email", "designation", "skills"],
        filters=[("pod", "Pod", "pod_id", lambda: [(p.pk, p.name) for p in models.Pod.objects.all()]),
                 ("active", "Status", "is_active", lambda: [("1", "Active"), ("0", "Inactive")])],
        detail=employee_detail, toggle="is_active", deletable=False, select_related=("pod",)),
    Cfg("brands", models.Brand, forms.BrandForm, "Brand", "Brands", "shop",
        [("ID", lambda o: link(o.brand_id, "brands", o)), ("Brand", lambda o: o.name),
         ("Team", lambda o: str(o.assignments.filter(is_active=True, is_backup=False).values("employee").distinct().count())),
         ("Processes", lambda o: str(o.rules.count())), ("Status", lambda o: active_badge(o.is_active))],
        search=["brand_id", "name", "description"], filters=[("status", "Status", "status", lambda: models.Brand.STATUS)],
        detail=brand_detail, toggle="status", deletable=False),
    Cfg("processes", models.Process, forms.ProcessForm, "Process", "Processes", "diagram-3",
        [("Code", lambda o: link(o.process_id, "processes", o)), ("Process", lambda o: o.name),
         ("Brands", lambda o: str(o.rules.count())), ("Status", lambda o: active_badge(o.is_active))],
        search=["process_id", "name"], filters=[("status", "Status", "status", lambda: models.Process.STATUS)],
        detail=process_detail, toggle="status", deletable=False),
    Cfg("pods", models.Pod, forms.PodForm, "Pod", "Pods", "grid-3x3-gap",
        [("ID", lambda o: link(o.pod_id, "pods", o)), ("Pod", lambda o: o.name), ("Lead", lambda o: str(o.lead or "—")),
         ("Members", lambda o: str(o.employees.filter(is_active=True).count()))],
        search=["pod_id", "name"], detail=pod_detail, select_related=("lead",), affects_roster=False),
    Cfg("assignments", models.Assignment, forms.AssignmentForm, "Assignment", "Assignments", "link-45deg",
        [("Employee", lambda o: link(o.employee.full_name, "assignments", o)), ("Brand", lambda o: o.brand.name),
         ("Process", lambda o: o.process.process_id), ("Role", lambda o: badge("Backup", "info") if o.is_backup else badge("Primary", "primary")),
         ("Status", lambda o: active_badge(o.is_active))],
        search=["employee__full_name", "employee__employee_id", "brand__name", "process__process_id"],
        filters=[("brand", "Brand", "brand_id", lambda: [(b.pk, b.name) for b in models.Brand.objects.all()]),
                 ("process", "Process", "process_id", lambda: [(p.pk, p.process_id) for p in models.Process.objects.all()]),
                 ("active", "Status", "is_active", lambda: [("1", "Active"), ("0", "Inactive")])],
        detail=simple_detail([("Employee", lambda o: o.employee), ("Brand", lambda o: o.brand), ("Process", lambda o: o.process),
                              ("Assigned date", lambda o: o.assigned_date or "—"), ("Role", lambda o: "Backup" if o.is_backup else "Primary"),
                              ("Active", lambda o: _yesno(o.is_active))]),
        select_related=("employee", "brand", "process")),
    Cfg("rules", models.OperatingRule, forms.RuleForm, "Operating rule", "Operating rules", "calendar-check",
        [("Brand", lambda o: link(o.brand.name, "rules", o)), ("Process", lambda o: o.process.process_id), ("Calendar", lambda o: o.calendar_display),
         ("Min staff", lambda o: str(o.min_staff)), ("Overrides", lambda o: o.weekday_min_staff or "—"), ("Shift", lambda o: o.shift_label or "—"),
         ("Status", lambda o: active_badge(o.is_active))],
        search=["brand__name", "process__process_id"],
        filters=[("brand", "Brand", "brand_id", lambda: [(b.pk, b.name) for b in models.Brand.objects.all()]),
                 ("process", "Process", "process_id", lambda: [(p.pk, p.process_id) for p in models.Process.objects.all()])],
        detail=simple_detail([("Brand", lambda o: o.brand), ("Process", lambda o: o.process), ("Calendar", lambda o: o.calendar_display),
                              ("Minimum staff", lambda o: o.min_staff), ("Weekday overrides", lambda o: o.weekday_min_staff or "—"),
                              ("Shift", lambda o: o.shift_label or "—"), ("Active", lambda o: _yesno(o.is_active))]),
        select_related=("brand", "process")),
    Cfg("holidays", models.Holiday, forms.HolidayForm, "Public holiday", "Public holidays", "calendar-event",
        [("Date", lambda o: link(o.date.strftime("%a %d %b %Y"), "holidays", o)), ("Holiday", lambda o: o.name),
         ("Applies to", lambda o: "All brands" if o.applies_to_all else ", ".join(b.name for b in o.brands.all()) or "No brand selected")],
        search=["name"], filters=[("year", "Year", "date__year", lambda: [(y, y) for y in sorted({d.year for d in models.Holiday.objects.dates("date", "year")})])],
        detail=simple_detail([("Date", lambda o: o.date), ("Name", lambda o: o.name),
                              ("Applies to", lambda o: "All brands" if o.applies_to_all else ", ".join(b.name for b in o.brands.all()) or "—")]),
        ordering=("date",)),
]
BY_SLUG = {c.slug: c for c in CONFIGS}


def _after_change(cfg, request, text):
    log_activity(request.user, text)
    if cfg.affects_roster:
        revalidate_existing()


def make_views(cfg):
    def list_view(request):
        qs = cfg.model.objects.all()
        if cfg.select_related:
            qs = qs.select_related(*cfg.select_related)
        q = request.GET.get("q", "").strip()
        if q and cfg.search:
            cond = Q()
            for f in cfg.search:
                cond |= Q(**{f"{f}__icontains": q})
            qs = qs.filter(cond)
        filter_ui = []
        for param, label, lookup, choices in cfg.filters:
            val = request.GET.get(param, "")
            if val != "":
                qs = qs.filter(**{lookup: (val == "1") if lookup == "is_active" else val})
            filter_ui.append({"param": param, "label": label, "value": val, "choices": [(str(a), b) for a, b in choices()]})
        if cfg.ordering:
            qs = qs.order_by(*cfg.ordering)
        page = Paginator(qs, 15).get_page(request.GET.get("page"))
        rows = [{"obj": o, "cells": [fn(o) for _, fn in cfg.columns]} for o in page]
        query = request.GET.copy()
        query.pop("page", None)
        return render(request, "crud/list.html", {"cfg": cfg, "page": page, "rows": rows, "headers": [h for h, _ in cfg.columns],
                                                   "filters": filter_ui, "q": q, "querystring": query.urlencode()})

    def create_view(request):
        form = cfg.form(request.POST or None)
        if request.method == "POST" and form.is_valid():
            obj = form.save()
            messages.success(request, f"{cfg.title} “{obj}” created.")
            _after_change(cfg, request, f"Created {cfg.title.lower()}: {obj}")
            return redirect(f"{cfg.slug}_detail", pk=obj.pk)
        return render(request, "crud/form.html", {"cfg": cfg, "form": form, "mode": "New"})

    def edit_view(request, pk):
        obj = get_object_or_404(cfg.model, pk=pk)
        form = cfg.form(request.POST or None, instance=obj)
        if request.method == "POST" and form.is_valid():
            obj = form.save()
            messages.success(request, f"{cfg.title} “{obj}” updated.")
            _after_change(cfg, request, f"Updated {cfg.title.lower()}: {obj}")
            return redirect(f"{cfg.slug}_detail", pk=obj.pk)
        return render(request, "crud/form.html", {"cfg": cfg, "form": form, "mode": "Edit", "obj": obj})

    def detail_view(request, pk):
        obj = get_object_or_404(cfg.model, pk=pk)
        fields, sections = cfg.detail(obj) if cfg.detail else ([], [])
        return render(request, "crud/detail.html", {"cfg": cfg, "obj": obj, "fields": fields, "sections": sections,
                                                     "is_active": getattr(obj, "is_active", None)})

    @require_POST
    def toggle_view(request, pk):
        obj = get_object_or_404(cfg.model, pk=pk)
        if cfg.toggle == "status":
            obj.status = "INACTIVE" if obj.status == "ACTIVE" else "ACTIVE"
            state = obj.get_status_display()
        else:
            obj.is_active = not obj.is_active
            state = "active" if obj.is_active else "inactive"
        obj.save()
        messages.success(request, f"{cfg.title} “{obj}” is now {state.lower()}.")
        _after_change(cfg, request, f"{cfg.title} {obj} set to {state.lower()}")
        return redirect(f"{cfg.slug}_detail", pk=pk)

    @require_POST
    def delete_view(request, pk):
        obj = get_object_or_404(cfg.model, pk=pk)
        label = str(obj)
        obj.delete()
        messages.success(request, f"{cfg.title} “{label}” deleted.")
        _after_change(cfg, request, f"Deleted {cfg.title.lower()}: {label}")
        return redirect(f"{cfg.slug}_list")

    return list_view, create_view, edit_view, detail_view, toggle_view, delete_view


def crud_urls():
    urls = []
    for cfg in CONFIGS:
        lv, cv, ev, dv, tv, xv = make_views(cfg)
        s = cfg.slug
        urls += [path(f"{s}/", lv, name=f"{s}_list"), path(f"{s}/new/", cv, name=f"{s}_new"),
                 path(f"{s}/<int:pk>/", dv, name=f"{s}_detail"), path(f"{s}/<int:pk>/edit/", ev, name=f"{s}_edit")]
        if cfg.toggle:
            urls.append(path(f"{s}/<int:pk>/toggle/", tv, name=f"{s}_toggle"))
        if cfg.deletable:
            urls.append(path(f"{s}/<int:pk>/delete/", xv, name=f"{s}_delete"))
    return urls
