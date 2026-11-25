from datetime import date

from django.urls import reverse

from apps.core.models import Conflict, Leave, RosterEntry
from apps.scheduler import services
from apps.scheduler.explain import candidate_replacements
from apps.scheduler.validation import run_validation

from .helpers import MONTH, YEAR, RosterTestCase


class ChangeTests(RosterTestCase):
    def setUp(self):
        super().setUp()
        self.owner, self.b = self.emp(1, off="6"), self.brand(1)           # Brand A: single dedicated employee
        self.assign(self.owner, self.b, self.cs)
        self.rule(self.b, self.cs, "SUN_HOL_OFF")
        self.peer = self.emp(2, off="6")                                    # handles CS for another brand -> eligible cover
        self.b2 = self.brand(2)
        self.assign(self.peer, self.b2, self.cs)
        self.rule(self.b2, self.cs, "SUN_HOL_OFF")
        self.generate()

    def test_baseline_has_no_critical_conflicts(self):
        self.assertFalse(self.conflicts(severity="CRITICAL").exists())

    def test_leave_after_generation_creates_critical_gap_with_replacement(self):
        r = self.client.post(reverse("leave_new"), {"employee": self.owner.pk, "start_date": "2027-03-10", "end_date": "2027-03-10",
                                                    "leave_type": "PLANNED", "status": "APPROVED", "reason": "test"})
        self.assertEqual(r.status_code, 302)
        gap = self.conflicts(conflict_type="NO_COVERAGE").get()
        self.assertEqual(gap.severity, "CRITICAL")
        self.assertEqual(gap.date, date(2027, 3, 10))
        self.assertIn("approved", gap.description.lower())
        self.assertIn(self.peer.full_name, gap.suggestion)              # replacement suggested
        self.assertTrue(self.conflicts(conflict_type="LEAVE_SCHEDULED", employee=self.owner).exists())  # stale roster row flagged

    def test_assigning_suggested_replacement_resolves_gap(self):
        leave = Leave.objects.create(employee=self.owner, start_date=date(2027, 3, 10), end_date=date(2027, 3, 10), status="APPROVED")
        run_validation(YEAR, MONTH)
        gap = self.conflicts(conflict_type="NO_COVERAGE").get()
        r = self.client.post(reverse("conflict_assign", args=[gap.pk]), {"employee": self.peer.pk})
        self.assertEqual(r.status_code, 302)
        self.assertFalse(self.conflicts(conflict_type="NO_COVERAGE").exists())
        self.client.post(reverse("leave_apply", args=[leave.pk]))
        self.assertEqual(RosterEntry.objects.get(employee=self.owner, date=date(2027, 3, 10)).status, "LEAVE")
        self.assertFalse(self.conflicts(severity__in=["CRITICAL", "HIGH"]).exists())

    def test_publish_blocked_by_critical_then_allowed_after_acknowledge(self):
        Leave.objects.create(employee=self.owner, start_date=date(2027, 3, 10), end_date=date(2027, 3, 10), status="APPROVED")
        run_validation(YEAR, MONTH)
        self.client.post(reverse("publish"), {"year": YEAR, "month": MONTH})
        from apps.core.models import RosterPeriod
        self.assertEqual(RosterPeriod.objects.get(year=YEAR, month=MONTH).status, "DRAFT")
        for c in self.conflicts(severity="CRITICAL"):
            self.client.post(reverse("conflict_ack", args=[c.pk]), {"note": "accepted"})
        self.client.post(reverse("publish"), {"year": YEAR, "month": MONTH})
        self.assertEqual(RosterPeriod.objects.get(year=YEAR, month=MONTH).status, "PUBLISHED")

    def test_availability_change_reapproval_flow(self):
        leave = Leave.objects.create(employee=self.owner, start_date=date(2027, 3, 10), end_date=date(2027, 3, 10), status="PENDING")
        run_validation(YEAR, MONTH)
        self.assertFalse(self.conflicts(conflict_type="NO_COVERAGE").exists())          # pending does not matter
        self.client.post(reverse("leave_action", args=[leave.pk, "approve"]))
        self.assertTrue(self.conflicts(conflict_type="NO_COVERAGE").exists())
        self.client.post(reverse("leave_action", args=[leave.pk, "reject"]))
        self.assertFalse(self.conflicts(conflict_type="NO_COVERAGE").exists())

    def test_multi_employee_brand_survives_one_absence(self):
        b3 = self.brand(3)
        a, c = self.emp(10, off="0"), self.emp(11, off="2")
        for e in (a, c):
            self.assign(e, b3, self.cs)
        self.rule(b3, self.cs, "SUN_HOL_OFF")
        self.generate()
        leave = Leave.objects.create(employee=a, start_date=date(2027, 3, 4), end_date=date(2027, 3, 4), status="APPROVED")
        res = run_validation(YEAR, MONTH)
        self.assertFalse([f for f in res["found"] if f["brand_id"] == b3.id and f["conflict_type"] in ("NO_COVERAGE", "INSUFFICIENT_STAFFING")])
        rows, _ = services.leave_impact(leave)
        self.assertEqual([r["state"] for r in rows], ["OK"])

    def test_manual_edit_invalid_assignment_needs_confirmation(self):
        stranger = self.emp(5, off="6")                      # not assigned to Brand A
        url = reverse("entry_edit", args=[stranger.pk, "2027-03-09"])
        data = {"status": "WORKING", "pairs": [f"{self.b.pk}-{self.cs.pk}"], "note": ""}
        r = self.client.post(url, data)
        self.assertEqual(r.status_code, 200)                  # re-rendered with warnings, not saved
        self.assertTrue(r.context["issues"])
        self.assertFalse(RosterEntry.objects.filter(employee=stranger, date=date(2027, 3, 9), source="MANUAL").exists())
        r = self.client.post(url, {**data, "confirm": "1"})
        self.assertEqual(r.status_code, 302)
        self.assertTrue(self.conflicts(conflict_type="NOT_ASSIGNED", employee=stranger).exists())  # saved but flagged

    def test_manual_edit_removing_only_cover_is_critical_and_revalidated(self):
        url = reverse("entry_edit", args=[self.owner.pk, "2027-03-09"])
        r = self.client.post(url, {"status": "UNASSIGNED", "note": ""})
        self.assertTrue(any(sev == "CRITICAL" for sev, _ in r.context["issues"]))
        self.client.post(url, {"status": "UNASSIGNED", "note": "", "confirm": "1"})
        self.assertTrue(self.conflicts(conflict_type="NO_COVERAGE", date=date(2027, 3, 9)).exists())
        self.assertEqual(RosterEntry.objects.get(employee=self.owner, date=date(2027, 3, 9)).source, "MANUAL")

    def test_manual_edits_survive_regeneration(self):
        url = reverse("entry_edit", args=[self.owner.pk, "2027-03-09"])
        self.client.post(url, {"status": "UNASSIGNED", "note": "x", "confirm": "1"})
        self.client.post(reverse("generate"), {"year": YEAR, "month": MONTH, "keep_manual": "1"})
        self.assertEqual(RosterEntry.objects.get(employee=self.owner, date=date(2027, 3, 9)).status, "UNASSIGNED")
        self.client.post(reverse("generate"), {"year": YEAR, "month": MONTH})
        self.assertEqual(RosterEntry.objects.get(employee=self.owner, date=date(2027, 3, 9)).status, "WORKING")

    def test_weekly_off_scheduled_is_conflict(self):
        url = reverse("entry_edit", args=[self.owner.pk, "2027-03-07"])  # Sunday = owner's weekly off
        self.client.post(url, {"status": "WORKING", "pairs": [f"{self.b.pk}-{self.cs.pk}"], "confirm": "1"})
        self.assertTrue(self.conflicts(conflict_type="WEEKLY_OFF_SCHEDULED", employee=self.owner).exists())

    def test_candidate_ranking_prefers_backup_then_same_process(self):
        from apps.scheduler.calendar_rules import MonthContext
        from apps.scheduler.explain import load_entry_map
        backup = self.emp(7, off="6")
        self.assign(backup, self.b, self.cs, backup=True)
        ctx = MonthContext(YEAR, MONTH)
        cands = candidate_replacements(ctx, load_entry_map(ctx), ctx.pair_rule[(self.b.id, self.cs.id)], date(2027, 3, 10))
        self.assertEqual(cands[0]["employee"], backup)

    def test_exports_match_saved_roster(self):
        r = self.client.get(reverse("roster_export", args=["csv"]) + f"?year={YEAR}&month={MONTH}&employee={self.owner.pk}")
        text = r.content.decode("utf-8-sig").splitlines()
        self.assertEqual(len(text), 2)                        # header + the one filtered employee
        self.assertIn("Tester 1", text[1])
        self.assertIn("Working (B1-CS)", text[1])
        self.assertEqual(self.client.get(reverse("roster_export", args=["xlsx"]) + f"?year={YEAR}&month={MONTH}").status_code, 200)
