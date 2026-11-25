"""python manage.py seed_data  — realistic, re-runnable sample data (all names/brands are fictional).

Assumptions (also documented in README):
  * 30 employees in 4 pods, 4 pod leads; 24 brands: 19 single-resource (79%) and 5 multi-resource (2-3 people).
  * Two employees (E05, E14) look after two brands each; some employees are designated backups.
  * Operating calendars, weekly offs, public holidays (several dates are assumed/fixed), leave and comp-offs are invented.
  * Rosters are generated for the current and next month.
Safe to re-run: every record is keyed by its business ID and updated in place.
"""
import os
from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core import models as M

POD_DEFS = [("POD-A", "Alpha Pod", 1), ("POD-B", "Bravo Pod", 9), ("POD-C", "Charlie Pod", 17), ("POD-D", "Delta Pod", 24)]
PROCESSES = [("CS", "Customer Support", "Inbound tickets, chats and calls."),
             ("ACC", "Abandoned Cart Conversion", "Outbound recovery of abandoned carts."),
             ("RET", "Retention", "Win-back and repeat-purchase outreach."),
             ("NDR", "Non-Delivery Report", "Resolving failed or delayed deliveries.")]
# (number, name, pod number, off_mode, weekly_off_days, preferred_off_days)
EMPLOYEES = [
    (1, "Aarav Mehta", 1, "FIXED", "0", ""), (2, "Diya Nair", 1, "FIXED", "0", ""), (3, "Kabir Shah", 1, "FIXED", "2", ""),
    (4, "Ishita Rao", 1, "FIXED", "6", ""), (5, "Rohan Kulkarni", 1, "FIXED", "6", ""), (6, "Sneha Iyer", 1, "FIXED", "6", ""),
    (7, "Vikram Joshi", 1, "FIXED", "2", ""), (8, "Anaya Desai", 1, "FIXED", "6", ""),
    (9, "Rahul Verma", 2, "FIXED", "6", ""), (10, "Priya Singh", 2, "ROTATING", "", "6"), (11, "Arjun Reddy", 2, "ROTATING", "", "2"),
    (12, "Meera Pillai", 2, "ROTATING", "", "0"), (13, "Siddharth Jain", 2, "FIXED", "5,6", ""), (14, "Tanvi Kapoor", 2, "FIXED", "6", ""),
    (15, "Karan Malhotra", 2, "FIXED", "0", ""), (16, "Nisha Gupta", 2, "FIXED", "6", ""),
    (17, "Aditya Bose", 3, "FIXED", "6", ""), (18, "Pooja Menon", 3, "FIXED", "6", ""), (19, "Yash Patel", 3, "FIXED", "0", ""),
    (20, "Riya Sharma", 3, "FIXED", "3", ""), (21, "Manish Agarwal", 3, "FIXED", "6", ""), (22, "Kavya Krishnan", 3, "FIXED", "6", ""),
    (23, "Dev Chopra", 3, "FIXED", "6", ""),
    (24, "Neha Banerjee", 4, "FIXED", "1", ""), (25, "Harsh Vora", 4, "FIXED", "6", ""), (26, "Simran Kaur", 4, "FIXED", "3", ""),
    (27, "Omkar Patil", 4, "FIXED", "6", ""), (28, "Tara Bhatt", 4, "FIXED", "6", ""), (29, "Farhan Qureshi", 4, "FIXED", "6", ""),
    (30, "Zoya Ansari", 4, "FIXED", "5,6", ""),
]
SUN = ("SUN_HOL_OFF", "")
ALL = ("ALL_365", "")
EXC = ("EXCEPT_HOLIDAYS", "")
# brand number -> (name, description, {process: (calendar, custom_days, min_staff, weekday_min, [primary emp nums], [backup emp nums])})
BRANDS = {
    1: ("Lumina Skincare", "Demo 'Brand A' — one dedicated employee for every process.", {"CS": (*SUN, 1, "", [6], []), "ACC": (*SUN, 1, "", [6], [])}),
    2: ("Urban Threads", "Fashion D2C. CS runs 365 days with a backup; ACC runs Tue–Sun.", {"CS": (*ALL, 1, "", [1], [7]), "ACC": ("CUSTOM", "1,2,3,4,5,6", 1, "", [1], [])}),
    3: ("Brewmaster Coffee", "Coffee subscriptions.", {"CS": (*SUN, 1, "", [5], []), "RET": (*SUN, 1, "", [5], [])}),
    4: ("FitFuel Nutrition", "Supplements.", {"CS": (*SUN, 1, "", [5], [])}),
    5: ("PetPals", "Pet supplies. Seven-day support, backup covers the owner's weekly off.", {"CS": (*EXC, 1, "", [7], [8]), "NDR": (*EXC, 1, "", [7], [8])}),
    6: ("Zenith Footwear", "Footwear.", {"CS": (*SUN, 1, "", [8], []), "ACC": (*SUN, 1, "", [8], [])}),
    7: ("Glow Organics", "Organic beauty.", {"CS": (*SUN, 1, "", [9], []), "NDR": (*SUN, 1, "", [9], [])}),
    8: ("Nomad Backpacks", "Weekday-only operation.", {"CS": ("CUSTOM", "0,1,2,3,4", 1, "", [13], []), "ACC": ("CUSTOM", "0,1,2,3,4", 1, "", [13], [])}),
    9: ("Spice Route Foods", "Gourmet foods.", {"CS": (*SUN, 1, "", [14], [])}),
    10: ("Aqua Home", "Home water solutions.", {"CS": (*SUN, 1, "", [14], []), "ACC": (*SUN, 1, "", [14], [])}),
    11: ("Velvet Home Decor", "Home decor, Tue–Sun.", {"CS": ("CUSTOM", "1,2,3,4,5,6", 1, "", [15], []), "RET": ("CUSTOM", "1,2,3,4,5,6", 1, "", [15], [])}),
    12: ("Pixel Gadgets", "Consumer electronics accessories.", {"CS": (*SUN, 1, "", [16], []), "NDR": (*SUN, 1, "", [16], []), "ACC": (*SUN, 1, "", [16], [])}),
    13: ("GreenLeaf Tea", "Tea.", {"CS": (*SUN, 1, "", [17], []), "ACC": (*SUN, 1, "", [17], [])}),
    14: ("Bloom Florals", "Flower delivery — support needed every day of the year.", {"CS": (*ALL, 1, "", [20], [21])}),
    15: ("Aarav Ayurveda", "Ayurvedic wellness.", {"CS": (*SUN, 1, "", [21], []), "ACC": (*SUN, 1, "", [21], [])}),
    16: ("Craft Candles", "Handmade candles.", {"CS": (*SUN, 1, "", [22], [])}),
    17: ("Kids Kingdom Toys", "Toys.", {"CS": (*SUN, 1, "", [23], []), "ACC": (*SUN, 1, "", [23], [])}),
    18: ("Sip & Co Beverages", "Beverages. Daily support except holidays.", {"CS": (*EXC, 1, "", [24], [27]), "ACC": ("CUSTOM", "0,2,3,4,5,6", 1, "", [24], [])}),
    19: ("Trail Outdoor", "Outdoor gear.", {"CS": (*SUN, 1, "", [27], []), "RET": (*SUN, 1, "", [27], [])}),
    20: ("Stellar Electronics", "Multi-resource: two on CS (two required on Saturdays), separate ACC and NDR owners.",
         {"CS": (*SUN, 1, "Sat=2", [2, 3], []), "ACC": (*SUN, 1, "", [4], []), "NDR": (*SUN, 1, "", [3, 4], [])}),
    21: ("Haven Furniture", "Multi-resource: three rotating-off agents share CS (two needed on weekends).",
         {"CS": (*ALL, 1, "Sat=2, Sun=2", [10, 11, 12], []), "RET": (*SUN, 1, "", [10, 12], [])}),
    22: ("Opal Jewellery", "Multi-resource: two agents with staggered fixed offs.",
         {"CS": (*EXC, 1, "", [18, 19], []), "ACC": (*SUN, 1, "", [18], []), "NDR": ("CUSTOM", "1,2,3,4,5,6", 1, "", [19], [])}),
    23: ("Swift Mobility", "Multi-resource: CS 365 days shared by two agents.",
         {"CS": (*ALL, 1, "", [25, 26], []), "RET": (*SUN, 1, "", [25], [])}),
    24: ("Mosaic Fashion", "Multi-resource: one agent per process.",
         {"CS": (*SUN, 1, "", [29], []), "ACC": ("CUSTOM", "0,1,2,3,4", 1, "", [30], []), "NDR": (*SUN, 1, "", [28], [])}),
}
HOLIDAYS = [(1, 1, "New Year's Day"), (1, 26, "Republic Day"), (3, 14, "Holi (assumed date)"), (8, 15, "Independence Day"),
            (10, 2, "Gandhi Jayanti"), (11, 8, "Diwali (assumed date)"), (12, 25, "Christmas")]


def on_or_after(y, m, day, weekday):
    d = date(y, m, day)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


class Command(BaseCommand):
    help = "Populate the database with realistic, fictional sample data (safe to re-run)."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing roster data first (users are kept).")
        parser.add_argument("--no-roster", action="store_true", help="Skip generating the current/next month rosters.")
        parser.add_argument("--create-admin", action="store_true", help="Create the demo admin user.")
        parser.add_argument("--admin-username", default="admin")

    def handle(self, *args, **o):
        if o["reset"]:
            for model in (M.Conflict, M.RosterEntry, M.RosterPeriod, M.Leave, M.CompOff, M.Holiday, M.Assignment, M.OperatingRule,
                          M.Process, M.Brand, M.Pod, M.Employee, M.ActivityLog):
                model.objects.all().delete()
            self.stdout.write("Existing roster data removed.")
        with transaction.atomic():
            self.seed_core()
            self.seed_availability()
        self.seed_admin(o)
        if not o["no_roster"]:
            from apps.scheduler.engine import generate_roster
            t = date.today()
            nxt = (t.year + (t.month == 12), t.month % 12 + 1)
            for y, m in ((t.year, t.month), nxt):
                r = generate_roster(y, m, keep_manual=False)
                self.stdout.write(f"Roster {y}-{m:02d}: {r['stats']['covered']}/{r['stats']['required']} coverage slots OK, {len(r['conflicts'])} issue(s) flagged.")
        self.stdout.write(self.style.SUCCESS("Sample data ready."))

    # ------------------------------------------------------------------
    def seed_core(self):
        pods = {}
        for pid, name, _ in POD_DEFS:
            pods[pid] = M.Pod.objects.update_or_create(pod_id=pid, defaults={"name": name, "description": f"{name} — customer operations team."})[0]
        procs = {c: M.Process.objects.update_or_create(process_id=c, defaults={"name": n, "description": d, "status": "ACTIVE"})[0] for c, n, d in PROCESSES}
        emps = {}
        skills = {}
        for code_map in [b[2] for b in BRANDS.values()]:
            for code, spec in code_map.items():
                for n in spec[4] + spec[5]:
                    skills.setdefault(n, set()).add(code)
        leads = {lead for _, _, lead in POD_DEFS}
        for n, name, podn, mode, off, pref in EMPLOYEES:
            pod = pods[POD_DEFS[podn - 1][0]]
            first, last = name.lower().split()
            emps[n] = M.Employee.objects.update_or_create(employee_id=f"EMP{n:03d}", defaults={
                "full_name": name, "email": f"{first}.{last}@example.com", "phone": f"+91 90000 {n:05d}", "pod": pod,
                "designation": "Pod Lead" if n in leads else ("Senior Executive" if n % 4 == 0 else "Customer Operations Executive"),
                "joining_date": date(2024, 1 + n % 12, 1 + n % 27), "employment_status": "FULL_TIME",
                "skills": ", ".join(sorted(skills.get(n, []))), "is_active": True, "off_mode": mode, "weekly_off_days": off, "preferred_off_days": pref})[0]
        for pid, _, lead in POD_DEFS:
            pods[pid].lead = emps[lead]
            pods[pid].save()
        for bn, (name, desc, rules) in BRANDS.items():
            brand = M.Brand.objects.update_or_create(brand_id=f"BR{bn:03d}", defaults={"name": name, "description": desc, "status": "ACTIVE"})[0]
            for code, (cal, custom, min_staff, wk, prim, back) in rules.items():
                M.OperatingRule.objects.update_or_create(brand=brand, process=procs[code], defaults={
                    "calendar_type": cal, "custom_days": custom, "holiday_off": True, "min_staff": min_staff, "weekday_min_staff": wk, "is_active": True})
                for n, is_backup in [(x, False) for x in prim] + [(x, True) for x in back]:
                    M.Assignment.objects.update_or_create(employee=emps[n], brand=brand, process=procs[code], defaults={
                        "assigned_date": emps[n].joining_date, "is_active": True, "is_backup": is_backup})
        self.emps, self.brands = emps, {bn: M.Brand.objects.get(brand_id=f"BR{bn:03d}") for bn in BRANDS}
        glow = self.brands[7]
        t = date.today()
        for y in (t.year, t.year + 1):
            for mo, d, name in HOLIDAYS:
                M.Holiday.objects.update_or_create(date=date(y, mo, d), name=name, defaults={"applies_to_all": True})
            h = M.Holiday.objects.update_or_create(date=date(y, 12, 12), name="Glow Organics client closure", defaults={"applies_to_all": False})[0]
            h.brands.set([glow])
        M.RosterSettings.get()

    def seed_availability(self):
        t = date.today()
        months = [(t.year, t.month), (t.year + (t.month == 12), t.month % 12 + 1)]
        E = self.emps
        for y, m in months:
            tag = f"{y}-{m:02d}"
            tue = on_or_after(y, m, 9, 1)
            rows = [
                (E[11], tue, tue, "PLANNED", "APPROVED", f"Family function ({tag})"),                      # Haven: 2 others keep coverage
                (E[26], on_or_after(y, m, 16, 1), on_or_after(y, m, 16, 1), "CASUAL", "APPROVED", f"Personal work ({tag})"),  # Swift: partner covers
                (E[6], on_or_after(y, m, 20, 2), on_or_after(y, m, 20, 2), "PLANNED", "PENDING", f"Awaiting approval ({tag})"),  # Brand A owner — approve to see a gap
                (E[16], on_or_after(y, m, 12, 3), on_or_after(y, m, 12, 3) + timedelta(days=1), "SICK", "PENDING", f"Medical appointment ({tag})"),
                (E[1], on_or_after(y, m, 22, 1), on_or_after(y, m, 22, 1), "CASUAL", "REJECTED", f"Rejected: month-end peak ({tag})"),
            ]
            for emp, s, e, kind, status, reason in rows:
                M.Leave.objects.update_or_create(employee=emp, start_date=s, end_date=e, defaults={"leave_type": kind, "status": status, "reason": reason})
            for emp, d, status, earned in ((E[3], on_or_after(y, m, 13, 4), "APPROVED", date(y, m, 1)), (E[12], on_or_after(y, m, 14, 4), "APPROVED", date(y, m, 2)),
                                           (E[13], on_or_after(y, m, 19, 0), "PENDING", date(y, m, 3))):
                M.CompOff.objects.update_or_create(employee=emp, date=d, defaults={"status": status, "earned_on": earned, "reason": "Worked on an off day"})

    def seed_admin(self, o):
        User = get_user_model()
        create = o["create_admin"] or (settings.DEBUG and not User.objects.exists())
        if not create:
            return
        username = o["admin_username"]
        password = os.environ.get("DEMO_ADMIN_PASSWORD") or ("Admin@12345" if settings.DEBUG else None)
        if not password:
            raise CommandError("Set DEMO_ADMIN_PASSWORD to create the demo admin when DEBUG is off.")
        if User.objects.filter(username=username).exists():
            self.stdout.write(f"User '{username}' already exists; password left unchanged.")
            return
        User.objects.create_superuser(username=username, email="admin@example.com", password=password)
        self.stdout.write(self.style.WARNING(f"Demo admin created: username '{username}' (password from DEMO_ADMIN_PASSWORD, or the documented dev default)."))
