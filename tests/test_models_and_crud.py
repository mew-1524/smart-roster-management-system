from datetime import date

from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.core.models import Assignment, Brand, Employee, OperatingRule, Process, RosterAssignment, RosterEntry

from .helpers import RosterTestCase


class CrudTests(RosterTestCase):
    def test_employee_creation_via_form(self):
        r = self.client.post(reverse("employees_new"), {
            "employee_id": "E900", "full_name": "New Hire", "email": "n@example.com", "pod": self.pod.pk, "employment_status": "FULL_TIME",
            "is_active": "on", "off_mode": "FIXED", "weekly_off_days": ["6"], "joining_date": "2026-01-05"})
        self.assertEqual(r.status_code, 302)
        e = Employee.objects.get(employee_id="E900")
        self.assertEqual(e.weekly_off_days, "6")
        self.assertEqual(e.pod, self.pod)

    def test_employee_deactivate_not_delete(self):
        e = self.emp(1)
        self.client.post(reverse("employees_toggle", args=[e.pk]))
        e.refresh_from_db()
        self.assertFalse(e.is_active)
        self.assertTrue(Employee.objects.filter(pk=e.pk).exists())

    def test_brand_creation(self):
        r = self.client.post(reverse("brands_new"), {"brand_id": "B77", "name": "Fresh Brand", "status": "ACTIVE"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Brand.objects.filter(brand_id="B77").exists())

    def test_process_creation(self):
        r = self.client.post(reverse("processes_new"), {"process_id": "NDR", "name": "Non-Delivery Report", "status": "ACTIVE"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Process.objects.filter(process_id="NDR").exists())

    def test_assignment_creation_creates_default_rule(self):
        e, b = self.emp(1), self.brand(1)
        r = self.client.post(reverse("assignments_new"), {"employee": e.pk, "brand": b.pk, "process": self.cs.pk, "is_active": "on"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(Assignment.objects.filter(employee=e, brand=b, process=self.cs).exists())
        self.assertTrue(OperatingRule.objects.filter(brand=b, process=self.cs).exists())

    def test_employee_works_multiple_brands_and_brand_has_multiple_employees(self):
        e1, e2, b1, b2 = self.emp(1), self.emp(2), self.brand(1), self.brand(2)
        for e, b in ((e1, b1), (e1, b2), (e2, b1)):
            self.assign(e, b, self.cs)
        self.assertEqual(e1.assignments.count(), 2)
        self.assertEqual(b1.assignments.count(), 2)

    def test_duplicate_assignment_prevented(self):
        e, b = self.emp(1), self.brand(1)
        self.assign(e, b, self.cs)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.assign(e, b, self.cs)

    def test_duplicate_roster_assignment_prevented(self):
        e, b = self.emp(1), self.brand(1)
        entry = RosterEntry.objects.create(employee=e, date=date(2027, 3, 2), status="WORKING")
        RosterAssignment.objects.create(entry=entry, brand=b, process=self.cs)
        with self.assertRaises(IntegrityError), transaction.atomic():
            RosterAssignment.objects.create(entry=entry, brand=b, process=self.cs)
        with self.assertRaises(IntegrityError), transaction.atomic():
            RosterEntry.objects.create(employee=e, date=date(2027, 3, 2), status="UNASSIGNED")

    def test_pages_require_login(self):
        self.client.logout()
        r = self.client.get("/employees/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r.url)
