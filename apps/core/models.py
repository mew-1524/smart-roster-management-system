"""Relational model for the Smart Roster system.

Key separation (assessment requirement): a brand/process *operating calendar* lives in OperatingRule,
while an *individual employee's schedule* lives in RosterEntry/RosterAssignment.
"""
import re

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse_days(value):
    return {int(x) for x in str(value or "").split(",") if x.strip().isdigit() and 0 <= int(x) <= 6}


def format_days(value):
    return ", ".join(DAY_NAMES[d] for d in sorted(parse_days(value))) or "None"


def parse_weekday_min(text):
    """'Sat=2, Sun=2' -> {5: 2, 6: 2}. Raises ValueError on bad input."""
    out = {}
    names = [n.lower() for n in DAY_NAMES]
    for part in re.split(r"[,;]", text or ""):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"'{part}' should look like Sat=2")
        key, val = [s.strip() for s in part.split("=", 1)]
        if key.isdigit():
            idx = int(key)
        elif key[:3].lower() in names:
            idx = names.index(key[:3].lower())
        else:
            raise ValueError(f"Unknown day '{key}'")
        if not 0 <= idx <= 6 or not val.isdigit():
            raise ValueError(f"'{part}' is not valid")
        out[idx] = int(val)
    return out


class CSVWeekdayFormField(forms.MultipleChoiceField):
    widget = forms.CheckboxSelectMultiple

    def __init__(self, **kwargs):
        kwargs["choices"] = [(str(i), n) for i, n in enumerate(DAY_NAMES)]
        kwargs["required"] = False
        super().__init__(**kwargs)

    def prepare_value(self, value):
        if isinstance(value, str):
            return [v for v in value.split(",") if v != ""]
        return value

    def clean(self, value):
        return ",".join(sorted(super().clean(value)))


class WeekdayCSVField(models.CharField):
    """Stores a set of weekdays (0=Mon..6=Sun) as 'a,b,c'."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_length", 20)
        kwargs.setdefault("blank", True)
        super().__init__(*args, **kwargs)

    def formfield(self, **kwargs):
        return CSVWeekdayFormField(label=self.verbose_name.capitalize(), help_text=self.help_text)


class Pod(models.Model):
    pod_id = models.CharField("Pod ID", max_length=20, unique=True)
    name = models.CharField(max_length=100)
    lead = models.ForeignKey("Employee", null=True, blank=True, on_delete=models.SET_NULL, related_name="led_pods")
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["pod_id"]

    def __str__(self):
        return self.name


class Employee(models.Model):
    EMPLOYMENT = [("FULL_TIME", "Full-time"), ("PART_TIME", "Part-time"), ("CONTRACT", "Contract"), ("PROBATION", "Probation")]
    OFF_MODE = [("FIXED", "Fixed weekdays"), ("ROTATING", "Rotating (system picks one off per week)")]

    employee_id = models.CharField("Employee ID", max_length=20, unique=True)
    full_name = models.CharField(max_length=120)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    pod = models.ForeignKey(Pod, null=True, blank=True, on_delete=models.SET_NULL, related_name="employees")
    designation = models.CharField(max_length=80, blank=True)
    joining_date = models.DateField(null=True, blank=True)
    employment_status = models.CharField(max_length=20, choices=EMPLOYMENT, default="FULL_TIME")
    skills = models.CharField(max_length=200, blank=True, help_text="Comma separated, e.g. CS, ACC, NDR")
    is_active = models.BooleanField("Active", default=True)
    off_mode = models.CharField("Weekly-off mode", max_length=10, choices=OFF_MODE, default="FIXED")
    weekly_off_days = WeekdayCSVField("weekly off days", help_text="Used when mode is Fixed.")
    preferred_off_days = WeekdayCSVField("preferred off days", help_text="Preference only; coverage always wins.")

    class Meta:
        ordering = ["employee_id"]
        indexes = [models.Index(fields=["is_active"])]

    def __str__(self):
        return f"{self.full_name} ({self.employee_id})"

    @property
    def off_day_set(self):
        return parse_days(self.weekly_off_days)

    @property
    def preferred_set(self):
        return parse_days(self.preferred_off_days)

    @property
    def weekly_off_display(self):
        return "Rotating" if self.off_mode == "ROTATING" else format_days(self.weekly_off_days)


class Brand(models.Model):
    STATUS = [("ACTIVE", "Active"), ("INACTIVE", "Inactive")]
    brand_id = models.CharField("Brand ID", max_length=20, unique=True)
    name = models.CharField("Brand name", max_length=120)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="ACTIVE")

    class Meta:
        ordering = ["brand_id"]

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.status == "ACTIVE"


class Process(models.Model):
    STATUS = Brand.STATUS
    process_id = models.CharField("Process ID", max_length=20, unique=True, help_text="Short code, e.g. CS, ACC, RET, NDR")
    name = models.CharField("Process name", max_length=120)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=STATUS, default="ACTIVE")

    class Meta:
        ordering = ["process_id"]
        verbose_name_plural = "processes"

    def __str__(self):
        return self.name

    @property
    def is_active(self):
        return self.status == "ACTIVE"


class OperatingRule(models.Model):
    """Brand-process operating calendar + staffing requirement (independent of any employee's schedule)."""
    CALENDAR = [
        ("ALL_365", "365 days (incl. Sundays & public holidays)"),
        ("EXCEPT_HOLIDAYS", "Every day except public holidays"),
        ("SUN_HOL_OFF", "Sundays & public holidays off"),
        ("CUSTOM", "Custom operating days"),
    ]
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="rules")
    process = models.ForeignKey(Process, on_delete=models.CASCADE, related_name="rules")
    calendar_type = models.CharField(max_length=20, choices=CALENDAR, default="EXCEPT_HOLIDAYS")
    custom_days = WeekdayCSVField("custom operating days", help_text="Used when calendar is Custom.")
    holiday_off = models.BooleanField("Closed on public holidays (custom calendar)", default=True)
    min_staff = models.PositiveSmallIntegerField("Minimum staff per operating day", default=1)
    weekday_min_staff = models.CharField("Weekday overrides", max_length=60, blank=True,
                                         help_text="Higher/lower minimum on specific days, e.g. Sat=2, Sun=2")
    shift_label = models.CharField("Shift", max_length=30, blank=True, help_text="Optional, e.g. Day or Night. Different shifts cannot be combined on one day.")
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        ordering = ["brand__brand_id", "process__process_id"]
        constraints = [models.UniqueConstraint(fields=["brand", "process"], name="uniq_rule_brand_process")]

    def __str__(self):
        return f"{self.brand.name} / {self.process.process_id}"

    def clean(self):
        try:
            parse_weekday_min(self.weekday_min_staff)
        except ValueError as exc:
            raise ValidationError({"weekday_min_staff": str(exc)})
        if self.calendar_type == "CUSTOM" and not parse_days(self.custom_days):
            raise ValidationError({"custom_days": "Choose at least one operating day for a custom calendar."})

    @property
    def custom_day_set(self):
        return parse_days(self.custom_days)

    def min_for_weekday(self, weekday):
        return parse_weekday_min(self.weekday_min_staff).get(weekday, self.min_staff)

    @property
    def calendar_display(self):
        if self.calendar_type == "CUSTOM":
            return f"Custom: {format_days(self.custom_days)}"
        return self.get_calendar_type_display()


class Assignment(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="assignments")
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="assignments")
    process = models.ForeignKey(Process, on_delete=models.CASCADE, related_name="assignments")
    assigned_date = models.DateField(null=True, blank=True)
    is_active = models.BooleanField("Active", default=True)
    is_backup = models.BooleanField("Backup resource", default=False,
                                    help_text="Backups are only rostered to cover gaps; they are not part of the primary team.")

    class Meta:
        ordering = ["brand__brand_id", "process__process_id", "employee__employee_id"]
        constraints = [models.UniqueConstraint(fields=["employee", "brand", "process"], name="uniq_assignment")]
        indexes = [models.Index(fields=["brand", "process", "is_active"])]

    def __str__(self):
        return f"{self.employee.employee_id} → {self.brand.name}/{self.process.process_id}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        OperatingRule.objects.get_or_create(brand=self.brand, process=self.process,
                                            defaults={"calendar_type": "EXCEPT_HOLIDAYS", "min_staff": 1})


class Holiday(models.Model):
    date = models.DateField()
    name = models.CharField(max_length=100)
    applies_to_all = models.BooleanField("Applies to all brands", default=True)
    brands = models.ManyToManyField(Brand, blank=True, related_name="holidays",
                                    help_text="Only used when 'applies to all brands' is unticked.")

    class Meta:
        ordering = ["date"]
        constraints = [models.UniqueConstraint(fields=["date", "name"], name="uniq_holiday")]

    def __str__(self):
        return f"{self.name} ({self.date})"


class Leave(models.Model):
    TYPE = [("PLANNED", "Planned leave"), ("CASUAL", "Casual leave"), ("SICK", "Sick leave"),
            ("EARNED", "Earned leave"), ("UNAVAILABLE", "Custom unavailable dates")]
    STATUS = [("PENDING", "Pending"), ("APPROVED", "Approved"), ("REJECTED", "Rejected")]
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="leaves")
    start_date = models.DateField()
    end_date = models.DateField()
    leave_type = models.CharField(max_length=15, choices=TYPE, default="PLANNED")
    status = models.CharField(max_length=10, choices=STATUS, default="PENDING")
    reason = models.CharField(max_length=200, blank=True)
    requested_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date"]
        indexes = [models.Index(fields=["status", "start_date"])]

    def __str__(self):
        return f"{self.employee.employee_id} {self.start_date}→{self.end_date} ({self.status})"

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "End date cannot be before the start date."})

    @property
    def days(self):
        return (self.end_date - self.start_date).days + 1


class CompOff(models.Model):
    STATUS = Leave.STATUS
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="comp_offs")
    date = models.DateField(help_text="The day the employee is compensated off.")
    earned_on = models.DateField(null=True, blank=True, help_text="Holiday/off-day that was worked.")
    status = models.CharField(max_length=10, choices=STATUS, default="PENDING")
    reason = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-date"]
        constraints = [models.UniqueConstraint(fields=["employee", "date"], name="uniq_compoff")]

    def __str__(self):
        return f"{self.employee.employee_id} comp-off {self.date}"


class RosterPeriod(models.Model):
    STATUS = [("DRAFT", "Draft"), ("PUBLISHED", "Published")]
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=10, choices=STATUS, default="DRAFT")
    generated_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [models.UniqueConstraint(fields=["year", "month"], name="uniq_period")]

    def __str__(self):
        return f"{self.year}-{self.month:02d}"


class RosterEntry(models.Model):
    """One row per employee per day (unique) holding the day status."""
    STATUS = [("WORKING", "Working"), ("WEEKLY_OFF", "Weekly Off"), ("LEAVE", "Approved Leave"),
              ("COMP_OFF", "Comp-Off"), ("HOLIDAY", "Public Holiday"), ("UNASSIGNED", "Unassigned")]
    SOURCE = [("GENERATED", "Generated"), ("MANUAL", "Manual")]
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="roster_entries")
    date = models.DateField()
    status = models.CharField(max_length=12, choices=STATUS, default="UNASSIGNED")
    shift = models.CharField(max_length=30, blank=True)
    source = models.CharField(max_length=10, choices=SOURCE, default="GENERATED")
    note = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "employee__employee_id"]
        constraints = [models.UniqueConstraint(fields=["employee", "date"], name="uniq_entry_employee_date")]
        indexes = [models.Index(fields=["date", "status"])]

    def __str__(self):
        return f"{self.employee.employee_id} {self.date} {self.status}"


class RosterAssignment(models.Model):
    """What a WORKING employee covers on a day (brand + process). Duplicates are blocked by the DB."""
    entry = models.ForeignKey(RosterEntry, on_delete=models.CASCADE, related_name="assignments")
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE)
    process = models.ForeignKey(Process, on_delete=models.CASCADE)
    is_cover = models.BooleanField("Backup cover", default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["entry", "brand", "process"], name="uniq_roster_assignment")]
        indexes = [models.Index(fields=["brand", "process"])]

    def __str__(self):
        return f"{self.entry} → {self.brand_id}/{self.process_id}"


class Conflict(models.Model):
    SEVERITY = [("CRITICAL", "Critical"), ("HIGH", "High"), ("MEDIUM", "Medium"), ("LOW", "Low")]
    STATUS = [("OPEN", "Open"), ("ACKNOWLEDGED", "Acknowledged")]
    TYPES = [
        ("NO_COVERAGE", "No coverage"), ("INSUFFICIENT_STAFFING", "Insufficient staffing"),
        ("LEAVE_SCHEDULED", "Scheduled during approved leave"), ("COMPOFF_SCHEDULED", "Scheduled during comp-off"),
        ("WEEKLY_OFF_SCHEDULED", "Scheduled on weekly off"), ("NOT_ASSIGNED", "Missing assignment"),
        ("NON_OPERATING", "Scheduled on a closed day"), ("SHIFT_CLASH", "Incompatible shifts"),
        ("OVERLOAD", "Daily workload exceeded"), ("NO_ASSIGNMENT", "No employee assigned"),
        ("CONFIG_INFEASIBLE", "Conflicting rule"), ("MAX_CONSECUTIVE", "Too many consecutive days"),
        ("PREFERENCE", "Preference not honoured"), ("INACTIVE_EMPLOYEE", "Inactive employee scheduled"),
    ]
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    conflict_type = models.CharField(max_length=30, choices=TYPES)
    severity = models.CharField(max_length=10, choices=SEVERITY)
    date = models.DateField(null=True, blank=True)
    brand = models.ForeignKey(Brand, null=True, blank=True, on_delete=models.CASCADE)
    process = models.ForeignKey(Process, null=True, blank=True, on_delete=models.CASCADE)
    employee = models.ForeignKey(Employee, null=True, blank=True, on_delete=models.CASCADE)
    description = models.TextField()
    suggestion = models.TextField(blank=True)
    status = models.CharField(max_length=14, choices=STATUS, default="OPEN")
    note = models.CharField(max_length=200, blank=True)
    key = models.CharField(max_length=120)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "id"]
        constraints = [models.UniqueConstraint(fields=["year", "month", "key"], name="uniq_conflict_key")]
        indexes = [models.Index(fields=["year", "month", "severity"])]

    @property
    def conflict_id(self):
        return f"CF-{self.year}{self.month:02d}-{self.pk:04d}"


class ActivityLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    text = models.CharField(max_length=250)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class RosterSettings(models.Model):
    organisation = models.CharField(max_length=100, default="Datastraw Operations")
    max_daily_workloads = models.PositiveSmallIntegerField("Max brand-process workloads per employee per day", default=4)
    max_consecutive_days = models.PositiveSmallIntegerField("Max consecutive working days", default=6)

    class Meta:
        verbose_name_plural = "roster settings"

    @classmethod
    def get(cls):
        return cls.objects.get_or_create(pk=1)[0]


def log_activity(user, text):
    ActivityLog.objects.create(user=user if getattr(user, "is_authenticated", False) else None, text=text[:250])
