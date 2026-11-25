"""MonthContext: loads everything the engine/validator needs for one month, once, and answers
calendar questions (is a process operating? how many staff are required? is an employee available?).

Brand/process operating calendars (OperatingRule) are deliberately separate from employee availability
(weekly offs, leave, comp-off): the first says what must be covered, the second says who can cover it.
"""
from calendar import monthrange
from collections import defaultdict
from datetime import date, timedelta

from apps.core.models import (Assignment, CompOff, Employee, Holiday, Leave, OperatingRule, RosterSettings)


class MonthContext:
    def __init__(self, year, month):
        self.year, self.month = year, month
        last = monthrange(year, month)[1]
        self.first, self.last = date(year, month, 1), date(year, month, last)
        self.dates = [self.first + timedelta(days=i) for i in range(last)]
        self.settings = RosterSettings.get()
        self.employees = {e.id: e for e in Employee.objects.filter(is_active=True).select_related("pod").order_by("employee_id")}
        self.rules = list(OperatingRule.objects.filter(is_active=True, brand__status="ACTIVE", process__status="ACTIVE")
                          .select_related("brand", "process").order_by("brand__brand_id", "process__process_id"))
        self.pair_rule = {(r.brand_id, r.process_id): r for r in self.rules}
        self.brands = {r.brand_id: r.brand for r in self.rules}
        self.processes = {r.process_id: r.process for r in self.rules}
        self.primary, self.backup = defaultdict(list), defaultdict(list)
        self.emp_pairs, self.emp_backup_pairs = defaultdict(list), defaultdict(list)
        for a in Assignment.objects.filter(is_active=True, employee__is_active=True).order_by("employee__employee_id"):
            pair = (a.brand_id, a.process_id)
            if pair not in self.pair_rule or (a.assigned_date and a.assigned_date > self.last):
                continue
            (self.backup if a.is_backup else self.primary)[pair].append(a.employee_id)
            (self.emp_backup_pairs if a.is_backup else self.emp_pairs)[a.employee_id].append(pair)
        self.holidays = defaultdict(list)
        for h in Holiday.objects.filter(date__range=(self.first, self.last)).prefetch_related("brands"):
            self.holidays[h.date].append((h.applies_to_all, {b.id for b in h.brands.all()}, h.name))
        self.blocked = {}
        for c in CompOff.objects.filter(status="APPROVED", date__range=(self.first, self.last)):
            self.blocked[(c.employee_id, c.date)] = ("COMP_OFF", "Approved comp-off")
        for lv in Leave.objects.filter(status="APPROVED", start_date__lte=self.last, end_date__gte=self.first):
            d = max(lv.start_date, self.first)
            while d <= min(lv.end_date, self.last):
                self.blocked[(lv.employee_id, d)] = ("LEAVE", f"Approved {lv.get_leave_type_display().lower()}")
                d += timedelta(days=1)

    # ---- calendar side -------------------------------------------------
    def is_holiday(self, d, brand_id=None):
        for applies_all, brand_ids, _ in self.holidays.get(d, ()):
            if applies_all or (brand_id is not None and brand_id in brand_ids):
                return True
        return False

    def holiday_name(self, d):
        return ", ".join(h[2] for h in self.holidays.get(d, ()))

    def operating(self, rule, d):
        hol = self.is_holiday(d, rule.brand_id)
        t = rule.calendar_type
        if t == "ALL_365":
            return True
        if t == "EXCEPT_HOLIDAYS":
            return not hol
        if t == "SUN_HOL_OFF":
            return d.weekday() != 6 and not hol
        return d.weekday() in rule.custom_day_set and not (rule.holiday_off and hol)

    def required(self, rule, d):
        return rule.min_for_weekday(d.weekday()) if self.operating(rule, d) else 0

    # ---- people side ---------------------------------------------------
    def unavailable(self, emp_id, d):
        """None if the employee can be rostered, else (roster_status, reason)."""
        emp = self.employees.get(emp_id)
        if emp is None:
            return ("UNASSIGNED", "Inactive employee")
        if emp.joining_date and d < emp.joining_date:
            return ("UNASSIGNED", "Not yet joined")
        if (emp_id, d) in self.blocked:
            return self.blocked[(emp_id, d)]
        if emp.off_mode == "FIXED" and d.weekday() in emp.off_day_set:
            return ("WEEKLY_OFF", "Weekly off")
        return None

    def holiday_for_employee(self, emp_id, d):
        if not self.holidays.get(d):
            return False
        brand_ids = {b for b, _ in self.emp_pairs.get(emp_id, [])}
        return any(a or (ids & brand_ids) for a, ids, _ in self.holidays[d])

    def shift_clash(self, existing_pairs, pair):
        new = self.pair_rule[pair].shift_label
        return bool(new) and any(self.pair_rule[q].shift_label and self.pair_rule[q].shift_label != new for q in existing_pairs)

    def pair_label(self, pair):
        b, p = self.brands.get(pair[0]), self.processes.get(pair[1])
        return f"{b.name if b else pair[0]} / {p.process_id if p else pair[1]}"
