from django import template

register = template.Library()
WEEKDAYS = "一二三四五六日"


@register.filter
def zhdate(d):
    """10/28 (三)"""
    if not d:
        return ""
    return f"{d.month}/{d.day} ({WEEKDAYS[d.weekday()]})"


@register.filter
def days_from(d, today):
    if not d or not today:
        return ""
    return (d - today).days
