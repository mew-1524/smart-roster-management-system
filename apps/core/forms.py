from django import forms

from .models import Assignment, Brand, CompOff, Employee, Holiday, Leave, OperatingRule, Pod, Process, RosterSettings


class BootstrapMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            w = f.widget
            if isinstance(w, (forms.CheckboxSelectMultiple,)):
                continue
            if isinstance(w, forms.CheckboxInput):
                w.attrs["class"] = "form-check-input"
            elif isinstance(w, (forms.Select, forms.SelectMultiple)):
                w.attrs["class"] = "form-select"
            else:
                w.attrs["class"] = "form-control"
            if isinstance(w, forms.Textarea):
                w.attrs["rows"] = 3


class DateInput(forms.DateInput):
    input_type = "date"

    def __init__(self, **kw):
        kw.setdefault("format", "%Y-%m-%d")
        super().__init__(**kw)


class EmployeeForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Employee
        fields = ["employee_id", "full_name", "email", "phone", "pod", "designation", "joining_date", "employment_status",
                  "skills", "is_active", "off_mode", "weekly_off_days", "preferred_off_days"]
        widgets = {"joining_date": DateInput()}


class BrandForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Brand
        fields = ["brand_id", "name", "description", "status"]


class ProcessForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Process
        fields = ["process_id", "name", "description", "status"]


class PodForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Pod
        fields = ["pod_id", "name", "lead", "description"]

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["lead"].queryset = Employee.objects.filter(is_active=True)


class AssignmentForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Assignment
        fields = ["employee", "brand", "process", "assigned_date", "is_active", "is_backup"]
        widgets = {"assigned_date": DateInput()}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True)


class RuleForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = OperatingRule
        fields = ["brand", "process", "calendar_type", "custom_days", "holiday_off", "min_staff", "weekday_min_staff",
                  "shift_label", "is_active"]


class HolidayForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Holiday
        fields = ["date", "name", "applies_to_all", "brands"]
        widgets = {"date": DateInput(), "brands": forms.CheckboxSelectMultiple}


class LeaveForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Leave
        fields = ["employee", "start_date", "end_date", "leave_type", "status", "reason"]
        widgets = {"start_date": DateInput(), "end_date": DateInput()}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True)
        self.fields["status"].help_text = "Only approved leave blocks scheduling."


class CompOffForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = CompOff
        fields = ["employee", "date", "earned_on", "status", "reason"]
        widgets = {"date": DateInput(), "earned_on": DateInput()}

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.fields["employee"].queryset = Employee.objects.filter(is_active=True)


class WeeklyOffForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = Employee
        fields = ["off_mode", "weekly_off_days", "preferred_off_days"]


class SettingsForm(BootstrapMixin, forms.ModelForm):
    class Meta:
        model = RosterSettings
        fields = ["organisation", "max_daily_workloads", "max_consecutive_days"]
