"""每月循環任務：在網站裡產生「這個月」和「下個月」那一次的待辦。

不需要另外開 cron 服務：每次打開儀表板時檢查一次，缺的補上。
Google Calendar 那邊是一個循環系列事件（見 gcal.push_recurring），不會一筆一筆建。
"""

from datetime import date

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import RecurringTask, Task


def _months_ahead(today: date, n: int = 2):
    y, m = today.year, today.month
    for _ in range(n):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def ensure_instances(today: date | None = None) -> int:
    today = today or timezone.localdate()
    created = 0
    for rt in RecurringTask.objects.filter(active=True).select_related("client"):
        for y, m in _months_ahead(today):
            due = rt.date_in(y, m)
            if due < rt.start_date:
                continue
            if Task.objects.filter(recurring=rt, due_date=due).exists():
                continue  # 包含被「刪除這一次」封存的
            try:
                with transaction.atomic():
                    Task.objects.create(
                        recurring=rt, due_date=due, title=rt.title, client=rt.client, notes=rt.notes
                    )
                created += 1
            except IntegrityError:
                pass  # 另一個 worker 剛好同時建好了
    return created


def refresh_future_instances(rt: RecurringTask, today: date | None = None) -> None:
    """改了循環任務的內容 → 還沒做的這次、下次跟著更新；日期規則變了就重建。"""
    today = today or timezone.localdate()
    future = Task.objects.filter(recurring=rt, done=False, archived=False, due_date__gte=today)
    if not rt.active:
        future.delete()
        return
    for t in future:
        if t.due_date != rt.date_in(t.due_date.year, t.due_date.month):
            t.delete()
        else:
            Task.objects.filter(pk=t.pk).update(title=rt.title, client=rt.client, notes=rt.notes)
    ensure_instances(today)
