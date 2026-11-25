from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.core.models import Assignment, Brand, Employee, OperatingRule, Pod, Process

YEAR, MONTH = 2027, 3  # 1 Mar 2027 is a Monday; Sundays: 7, 14, 21, 28


class RosterTestCase(TestCase):
    def setUp(self):
        self.pod = Pod.objects.create(pod_id="P1", name="Alpha Pod")
        self.cs = Process.objects.create(process_id="CS", name="Customer Support")
        self.acc = Process.objects.create(process_id="ACC", name="Abandoned Cart")
        self.user = get_user_model().objects.create_user("mgr", password="pw-for-tests-123")
        self.client.force_login(self.user)

    def emp(self, n, off="6", mode="FIXED", **kw):
        return Employee.objects.create(employee_id=f"T{n:03d}", full_name=f"Tester {n}", pod=self.pod, off_mode=mode,
                                       weekly_off_days=off, joining_date=date(2025, 1, 1), **kw)

    def brand(self, n):
        return Brand.objects.create(brand_id=f"B{n}", name=f"Brand {n}")

    def rule(self, brand, proc, cal="SUN_HOL_OFF", min_staff=1, **kw):
        return OperatingRule.objects.update_or_create(brand=brand, process=proc, defaults=dict(calendar_type=cal, min_staff=min_staff, **kw))[0]

    def assign(self, emp, brand, proc, backup=False):
        return Assignment.objects.create(employee=emp, brand=brand, process=proc, is_backup=backup)

    def generate(self):
        from apps.scheduler.engine import generate_roster
        return generate_roster(YEAR, MONTH, keep_manual=False)

    def conflicts(self, **filters):
        from apps.core.models import Conflict
        return Conflict.objects.filter(year=YEAR, month=MONTH, **filters)
