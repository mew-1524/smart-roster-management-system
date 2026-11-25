import os
from datetime import date
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.contrib.auth import get_user_model

from apps.core.models import Assignment, Brand, Conflict, Employee, Pod
from apps.scheduler import services


class SeedAndDemoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        with mock.patch.dict(os.environ, {"DEMO_ADMIN_PASSWORD": "test-only-pass-123"}):
            call_command("seed_data", "--create-admin", verbosity=0)

    def test_dataset_shape(self):
        self.assertGreaterEqual(Employee.objects.count(), 30)
        self.assertEqual(Pod.objects.count(), 4)
        self.assertTrue(all(p.lead for p in Pod.objects.all()))
        single = sum(1 for b in Brand.objects.all()
                     if Assignment.objects.filter(brand=b, is_backup=False).values("employee").distinct().count() == 1)
        self.assertGreaterEqual(single / Brand.objects.count(), 0.75)
        self.assertLessEqual(single / Brand.objects.count(), 0.85)

    def test_seed_is_rerunnable_without_duplicates(self):
        before = (Employee.objects.count(), Brand.objects.count(), Assignment.objects.count())
        call_command("seed_data", "--no-roster", verbosity=0)
        self.assertEqual(before, (Employee.objects.count(), Brand.objects.count(), Assignment.objects.count()))

    def test_baseline_roster_has_no_critical_conflicts(self):
        self.assertFalse(Conflict.objects.filter(severity__in=["CRITICAL", "HIGH"]).exists())

    def test_demo_scenario_gap_for_brand_a_and_coverage_kept_for_multi_brand(self):
        res = services.run_demo_scenario()
        self.assertEqual(len(res["leaves"]), 2)
        gaps = Conflict.objects.filter(conflict_type="NO_COVERAGE", brand__brand_id="BR001")
        self.assertTrue(gaps.exists())
        self.assertTrue(all(g.severity == "CRITICAL" and g.suggestion for g in gaps))
        self.assertFalse(Conflict.objects.filter(conflict_type__in=["NO_COVERAGE", "INSUFFICIENT_STAFFING"], brand__brand_id="BR020").exists())
        services.reset_demo()
        self.assertFalse(Conflict.objects.filter(severity="CRITICAL").exists())

    def test_all_main_pages_render(self):
        c = self.client
        c.force_login(get_user_model().objects.get(username="admin"))
        t = date.today()
        q = f"?year={t.year}&month={t.month}"
        for url in ["/", "/employees/", "/brands/", "/processes/", "/pods/", "/assignments/", "/rules/", "/holidays/", "/availability/",
                    "/availability/?tab=compoffs", "/availability/?tab=weekly", "/roster/" + q, "/roster/generate/", "/roster/conflicts/" + q,
                    "/reports/", "/reports/employee/" + q.replace("?", "?"), "/reports/brand/" + q, "/reports/pod/" + q, "/reports/conflicts/" + q, "/settings/",
                    "/search/?q=a"]:
            self.assertEqual(c.get(url).status_code, 200, url)
