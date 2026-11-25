from datetime import date

from .models import Conflict, Leave, RosterSettings

NAV = [
    ("Dashboard", "speedometer2", "/", "exact"),
    ("Employees", "people", "/employees/", ""),
    ("Brands", "shop", "/brands/", ""),
    ("Processes", "diagram-3", "/processes/", ""),
    ("Pods", "grid-3x3-gap", "/pods/", ""),
    ("Assignments", "link-45deg", "/assignments/", ""),
    ("Availability & Leaves", "calendar2-x", "/availability/", ""),
    ("Public Holidays", "calendar-event", "/holidays/", ""),
    ("Roster Generator", "magic", "/roster/generate/", ""),
    ("Monthly Roster", "calendar3", "/roster/", "roster"),
    ("Conflicts & Validation", "exclamation-triangle", "/roster/conflicts/", ""),
    ("Reports", "bar-chart-line", "/reports/", ""),
    ("Settings", "gear", "/settings/", ""),
]


def shell(request):
    if not request.user.is_authenticated:
        return {}
    path = request.path
    items = []
    for label, icon, url, mode in NAV:
        if mode == "exact":
            active = path == url
        elif mode == "roster":
            active = path.startswith("/roster/") and not path.startswith(("/roster/generate", "/roster/conflicts"))
        else:
            active = path.startswith(url) and not (url == "/employees/" and False)
        items.append({"label": label, "icon": icon, "url": url, "active": active})
    pending = Leave.objects.filter(status="PENDING").select_related("employee")[:5]
    critical = Conflict.objects.filter(severity="CRITICAL", status="OPEN")
    today = date.today()
    return {"nav_items": items, "pending_leave_count": Leave.objects.filter(status="PENDING").count(),
            "critical_count": critical.count(), "notif_pending": pending, "notif_critical": critical.select_related("brand", "process")[:5],
            "org_name": RosterSettings.get().organisation, "today": today}
