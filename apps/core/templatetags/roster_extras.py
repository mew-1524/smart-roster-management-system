from django import template

register = template.Library()

STATUS_META = {
    "WORKING": ("W", "st-work", "Working"), "WEEKLY_OFF": ("WO", "st-off", "Weekly Off"), "LEAVE": ("L", "st-leave", "Approved Leave"),
    "COMP_OFF": ("CO", "st-comp", "Comp-Off"), "HOLIDAY": ("PH", "st-hol", "Public Holiday"), "UNASSIGNED": ("–", "st-none", "Unassigned"),
}
SEV_CLASS = {"CRITICAL": "danger", "HIGH": "orange", "MEDIUM": "warning", "LOW": "secondary"}


@register.filter
def get_item(d, key):
    try:
        return d.get(key)
    except AttributeError:
        return None


@register.filter
def status_code(s):
    return STATUS_META.get(s, ("?", "st-none", s))[0]


@register.filter
def status_class(s):
    return STATUS_META.get(s, ("?", "st-none", s))[1]


@register.filter
def sev_class(s):
    return SEV_CLASS.get(s, "secondary")


@register.filter
def kind(cell):
    return cell[0] if isinstance(cell, tuple) else "text"


@register.filter
def idx(seq, i):
    try:
        return seq[i]
    except (IndexError, TypeError, KeyError):
        return None


@register.filter
def pct(a, b):
    return round(100 * a / b) if b else 0
