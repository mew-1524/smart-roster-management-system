"""Read-side helpers: builds the filtered roster grid, coverage matrix and employee calendar used by
the Monthly Roster page, the exports and the reports (so exports always match what is on screen)."""
from calendar import monthrange
from collections import defaultdict
from datetime import date

from apps.core.models import Conflict

from .calendar_rules import MonthContext
from .explain import entry_pairs, load_entry_map

MODES = ("company", "pod", "brand", "process", "employee")
STATUS_CODE = {"WORKING": "W", "WEEKLY_OFF": "WO", "LEAVE": "L", "COMP_OFF": "CO", "HOLIDAY": "PH", "UNASSIGNED": "–"}
STATUS_CLS = {"WORKING": "st-work", "WEEKLY_OFF": "st-off", "LEAVE": "st-leave", "COMP_OFF": "st-comp", "HOLIDAY": "st-hol", "UNASSIGNED": "st-none"}


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class RosterView:
    def __init__(self, params):
        today = date.today()
        self.year = _int(params.get("year"), today.year)
        self.month = _int(params.get("month"), today.month)
        if not (1 <= self.month <= 12 and 2000 <= self.year <= 2100):
            self.year, self.month = today.year, today.month
        self.ctx = ctx = MonthContext(self.year, self.month)
        self.emap = load_entry_map(ctx)
        self.mode = params.get("view") if params.get("view") in MODES else "company"
        self.f = {k: _int(params.get(k)) for k in ("employee", "pod", "brand", "process")}
        last = monthrange(self.year, self.month)[1]
        d1, d2 = _int(params.get("from"), 1), _int(params.get("to"), last)
        d1, d2 = max(1, min(d1, last)), max(1, min(d2, last))
        if d2 < d1:
            d1, d2 = d2, d1
        self.d1, self.d2 = d1, d2
        self.dates = [d for d in ctx.dates if d1 <= d.day <= d2]
        self.employees = self._employees()
        self.pairs = self._pairs()
        conflicts = list(Conflict.objects.filter(year=self.year, month=self.month))
        self.conflict_cells = {(c.employee_id, c.date) for c in conflicts if c.employee_id and c.date}
        self.workers = defaultdict(set)
        for (emp_id, d), en in self.emap.items():
            if en.status == "WORKING" and ctx.unavailable(emp_id, d) is None:
                for p in entry_pairs(en):
                    if p in ctx.emp_pairs[emp_id] or p in ctx.emp_backup_pairs[emp_id]:
                        self.workers[(p, d)].add(emp_id)

    @property
    def roster_exists(self):
        return bool(self.emap)

    def _employees(self):
        ctx, f = self.ctx, self.f
        emps = list(ctx.employees.values())
        if f["employee"]:
            emps = [e for e in emps if e.id == f["employee"]]
        if f["pod"]:
            emps = [e for e in emps if e.pod_id == f["pod"]]
        if f["brand"] or f["process"]:
            def match(e):
                return any((not f["brand"] or p[0] == f["brand"]) and (not f["process"] or p[1] == f["process"])
                           for p in ctx.emp_pairs[e.id] + ctx.emp_backup_pairs[e.id])
            emps = [e for e in emps if match(e)]
        return emps

    def _pairs(self):
        ctx, f = self.ctx, self.f
        out = []
        for r in ctx.rules:
            p = (r.brand_id, r.process_id)
            if f["brand"] and r.brand_id != f["brand"]:
                continue
            if f["process"] and r.process_id != f["process"]:
                continue
            team = ctx.primary[p] + ctx.backup[p]
            if f["pod"] and not any(ctx.employees[e].pod_id == f["pod"] for e in team):
                continue
            if f["employee"] and f["employee"] not in team:
                continue
            out.append(r)
        return out

    def day_headers(self):
        return [{"d": d, "day": d.day, "wd": d.strftime("%a")[:2], "sun": d.weekday() == 6, "hol": self.ctx.holiday_name(d)} for d in self.dates]

    def cell(self, emp, d):
        en = self.emap.get((emp.id, d))
        flagged = (emp.id, d) in self.conflict_cells
        if en is None:
            return {"code": "?", "cls": "st-none", "title": f"{emp.full_name} {d:%d %b}: no roster entry", "flag": flagged, "status": "MISSING", "manual": False, "d": d}
        labels = [self.ctx.pair_label(p) for p in entry_pairs(en) if p in self.ctx.pair_rule]
        title = f"{emp.full_name} · {d:%a %d %b}: {en.get_status_display()}"
        if labels:
            title += " → " + "; ".join(labels)
        if en.note:
            title += f" ({en.note})"
        return {"code": STATUS_CODE[en.status], "cls": STATUS_CLS[en.status], "title": title, "flag": flagged, "status": en.status,
                "manual": en.source == "MANUAL", "d": d, "n": len(labels)}

    def grid_rows(self):
        return [{"emp": e, "cells": [self.cell(e, d) for d in self.dates],
                 "work": sum(1 for d in self.dates if (self.emap.get((e.id, d)) and self.emap[(e.id, d)].status == "WORKING"))}
                for e in self.employees]

    def grouped_rows(self):
        rows, groups = self.grid_rows(), []
        by_pod = defaultdict(list)
        for r in rows:
            by_pod[r["emp"].pod].append(r)
        for pod in sorted(by_pod, key=lambda p: (p is None, p.pod_id if p else "")):
            groups.append({"pod": pod, "rows": by_pod[pod]})
        return groups

    def coverage_rows(self):
        ctx, rows = self.ctx, []
        for rule in self.pairs:
            p = (rule.brand_id, rule.process_id)
            cells, bad = [], 0
            for d in self.dates:
                need = ctx.required(rule, d)
                if not need:
                    cells.append({"state": "closed", "text": "", "title": f"{ctx.pair_label(p)} closed on {d:%a %d %b}", "d": d})
                    continue
                have = len(self.workers[(p, d)])
                state = "ok" if have >= need else ("short" if have else "gap")
                bad += state != "ok"
                names = ", ".join(ctx.employees[e].full_name for e in sorted(self.workers[(p, d)])) or "nobody"
                cells.append({"state": state, "text": f"{have}/{need}", "d": d, "title": f"{ctx.pair_label(p)} {d:%a %d %b}: {have} of {need} · {names}"})
            rows.append({"label": ctx.brands[rule.brand_id].name, "process": ctx.processes[rule.process_id].process_id, "rule": rule, "cells": cells, "bad": bad})
        return rows

    def employee_calendar(self):
        if not self.employees or not self.f["employee"]:
            return None
        emp = self.employees[0]
        first_wd = self.ctx.first.weekday()
        flat = [None] * first_wd + [self.cell(emp, d) for d in self.ctx.dates]
        flat += [None] * (-len(flat) % 7)
        counts = defaultdict(int)
        for d in self.ctx.dates:
            en = self.emap.get((emp.id, d))
            counts[en.get_status_display() if en else "No entry"] += 1
        return {"emp": emp, "weeks": [flat[i:i + 7] for i in range(0, len(flat), 7)], "counts": dict(counts)}

    # ---- flat export ------------------------------------------------------------
    def export_grid(self):
        header = ["Employee ID", "Name", "Pod", "Weekly off"] + [f"{d.day} {d:%a}" for d in self.dates]
        rows = []
        for e in self.employees:
            line = [e.employee_id, e.full_name, e.pod.name if e.pod else "", e.weekly_off_display]
            for d in self.dates:
                en = self.emap.get((e.id, d))
                if en is None:
                    line.append("No entry")
                    continue
                labels = [f"{self.ctx.brands[b].brand_id}-{self.ctx.processes[pr].process_id}" for b, pr in entry_pairs(en)
                          if b in self.ctx.brands and pr in self.ctx.processes]
                line.append(en.get_status_display() + (f" ({'; '.join(labels)})" if labels else ""))
            rows.append(line)
        return header, rows

    def export_coverage(self):
        header = ["Brand", "Process"] + [f"{d.day} {d:%a}" for d in self.dates]
        rows = [[r["label"], r["process"]] + [c["text"] or "closed" for c in r["cells"]] for r in self.coverage_rows()]
        return header, rows
