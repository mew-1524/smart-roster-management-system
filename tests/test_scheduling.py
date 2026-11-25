from datetime import date

from apps.core.models import Conflict, Holiday, Leave, CompOff, RosterEntry
from apps.scheduler.engine import generate_roster
from apps.scheduler.validation import run_validation

from .helpers import MONTH, YEAR, RosterTestCase


def status(emp, d):
    return RosterEntry.objects.get(employee=emp, date=d).status


class SchedulingTests(RosterTestCase):
    def single_brand(self, cal="SUN_HOL_OFF"):
        e, b = self.emp(1, off="6"), self.brand(1)
        self.assign(e, b, self.cs)
        self.rule(b, self.cs, cal)
        return e, b

    def test_single_employee_brand_fully_covered(self):
        e, b = self.single_brand()
        res = self.generate()
        self.assertEqual(res["stats"]["gap"], 0)
        self.assertEqual(res["stats"]["covered"], res["stats"]["required"])
        self.assertEqual(self.conflicts(severity="CRITICAL").count(), 0)

    def test_multi_employee_brand_covered_with_staggered_offs(self):
        b = self.brand(1)
        e1, e2 = self.emp(1, off="0"), self.emp(2, off="3")
        for e in (e1, e2):
            self.assign(e, b, self.cs)
        self.rule(b, self.cs, "ALL_365")
        res = self.generate()
        self.assertEqual(res["stats"]["covered"], 31)
        self.assertEqual(status(e1, date(2027, 3, 1)), "WEEKLY_OFF")
        self.assertEqual(status(e2, date(2027, 3, 1)), "WORKING")

    def test_sunday_off_rule_means_no_required_coverage_on_sundays(self):
        e, b = self.emp(1, off="1"), self.brand(1)  # employee's own off is Tuesday, process closed Sunday
        self.assign(e, b, self.cs)
        self.rule(b, self.cs, "SUN_HOL_OFF")
        res = self.generate()
        self.assertNotEqual(status(e, date(2027, 3, 7)), "WORKING")
        self.assertEqual(res["stats"]["required"], 31 - 4)  # 4 Sundays closed
        self.assertEqual(res["stats"]["covered"] + res["stats"]["gap"], res["stats"]["required"])

    def test_365_rule_requires_sundays_and_holidays(self):
        e, b = self.emp(1, off="2"), self.brand(1)
        backup = self.emp(2, off="6")
        self.assign(e, b, self.cs)
        self.assign(backup, b, self.cs, backup=True)
        self.rule(b, self.cs, "ALL_365")
        Holiday.objects.create(date=date(2027, 3, 10), name="Test Holiday")  # a Wednesday: employee's weekly off
        res = self.generate()
        self.assertEqual(res["stats"]["required"], 31)
        self.assertEqual(status(e, date(2027, 3, 7)), "WORKING")      # Sunday worked
        self.assertEqual(status(backup, date(2027, 3, 10)), "WORKING")  # backup covers owner's off on the holiday
        self.assertEqual(res["stats"]["gap"], 0)

    def test_public_holiday_rule_closes_process(self):
        e, b = self.single_brand("EXCEPT_HOLIDAYS")
        Holiday.objects.create(date=date(2027, 3, 3), name="Test Holiday")
        res = self.generate()
        self.assertEqual(status(e, date(2027, 3, 3)), "HOLIDAY")
        # 7-day brand, owner off Sundays, no backup: only the 4 Sundays are uncovered - the holiday is not a gap
        gaps = sorted(self.conflicts(conflict_type="NO_COVERAGE").values_list("date", flat=True))
        self.assertEqual([d.weekday() for d in gaps], [6, 6, 6, 6])
        self.assertNotIn(date(2027, 3, 3), gaps)

    def test_brand_specific_holiday_only_applies_to_that_brand(self):
        e1, b1 = self.single_brand("EXCEPT_HOLIDAYS")
        e2, b2 = self.emp(2, off="6"), self.brand(2)
        self.assign(e2, b2, self.cs)
        self.rule(b2, self.cs, "EXCEPT_HOLIDAYS")
        h = Holiday.objects.create(date=date(2027, 3, 4), name="Client closure", applies_to_all=False)
        h.brands.set([b1])
        self.generate()
        self.assertEqual(status(e1, date(2027, 3, 4)), "HOLIDAY")
        self.assertEqual(status(e2, date(2027, 3, 4)), "WORKING")

    def test_approved_leave_excluded_pending_is_not(self):
        e, b = self.single_brand()
        Leave.objects.create(employee=e, start_date=date(2027, 3, 3), end_date=date(2027, 3, 3), status="APPROVED")
        Leave.objects.create(employee=e, start_date=date(2027, 3, 4), end_date=date(2027, 3, 4), status="PENDING")
        self.generate()
        self.assertEqual(status(e, date(2027, 3, 3)), "LEAVE")
        self.assertEqual(status(e, date(2027, 3, 4)), "WORKING")
        self.assertEqual(self.conflicts(conflict_type="NO_COVERAGE").count(), 1)

    def test_comp_off_handling(self):
        b = self.brand(1)
        e1, e2 = self.emp(1, off="6"), self.emp(2, off="6")
        for e in (e1, e2):
            self.assign(e, b, self.cs)
        self.rule(b, self.cs)
        CompOff.objects.create(employee=e1, date=date(2027, 3, 9), status="APPROVED")
        CompOff.objects.create(employee=e2, date=date(2027, 3, 16), status="PENDING")
        res = self.generate()
        self.assertEqual(status(e1, date(2027, 3, 9)), "COMP_OFF")
        self.assertEqual(status(e2, date(2027, 3, 16)), "WORKING")  # pending comp-off does not block
        self.assertEqual(res["stats"]["gap"], 0)

    def test_minimum_staffing_validation(self):
        e, b = self.single_brand()
        self.rule(b, self.cs, "SUN_HOL_OFF", min_staff=2)
        self.generate()
        c = self.conflicts(conflict_type="INSUFFICIENT_STAFFING")
        self.assertEqual(c.count(), 27)
        self.assertEqual(c.first().severity, "HIGH")

    def test_weekday_override_minimum(self):
        b = self.brand(1)
        e1, e2 = self.emp(1, off="0"), self.emp(2, off="2")
        for e in (e1, e2):
            self.assign(e, b, self.cs)
        self.rule(b, self.cs, "SUN_HOL_OFF", weekday_min_staff="Sat=2")
        res = self.generate()
        self.assertEqual(res["stats"]["gap"] + res["stats"]["insufficient"], 0)

    def test_missing_coverage_when_nobody_assigned(self):
        b, other = self.brand(1), self.brand(2)
        self.rule(b, self.cs)
        self.assign(self.emp(1), other, self.cs)  # some roster must exist for validation to run
        self.generate()
        self.assertTrue(self.conflicts(conflict_type="NO_ASSIGNMENT", severity="CRITICAL").exists())

    def test_rotating_offs_keep_coverage_and_rest_days(self):
        b = self.brand(1)
        es = [self.emp(i, off="", mode="ROTATING") for i in (1, 2, 3)]
        for e in es:
            self.assign(e, b, self.cs)
        self.rule(b, self.cs, "ALL_365")
        res = self.generate()
        self.assertEqual(res["stats"]["gap"], 0)
        for e in es:
            offs = RosterEntry.objects.filter(employee=e, status="WEEKLY_OFF").count()
            self.assertGreaterEqual(offs, 4)
            self.assertLessEqual(offs, 5)

    def test_incompatible_shifts_not_combined(self):
        e, b1, b2 = self.emp(1, off="6"), self.brand(1), self.brand(2)
        for b, shift in ((b1, "Day"), (b2, "Night")):
            self.assign(e, b, self.cs)
            self.rule(b, self.cs, "SUN_HOL_OFF", shift_label=shift)
        self.generate()
        entry = RosterEntry.objects.get(employee=e, date=date(2027, 3, 2))
        self.assertEqual(entry.assignments.count(), 1)           # engine refuses the clash...
        self.assertTrue(self.conflicts(conflict_type="NO_COVERAGE").exists())  # ...and reports the uncovered side

    def test_inactive_brand_is_not_scheduled(self):
        e, b = self.single_brand()
        b.status = "INACTIVE"
        b.save()
        res = self.generate()
        self.assertEqual(res["stats"]["required"], 0)
