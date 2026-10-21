import calendar
from datetime import date

from django.db import models
from django.db.models import Q
from django.utils import timezone

# Google Calendar 的事件顏色（colorId 1–11）
GCAL_COLORS = [
    ("", "日曆預設色"),
    ("1", "薰衣草紫"),
    ("2", "鼠尾草綠"),
    ("3", "葡萄紫"),
    ("4", "紅鶴粉"),
    ("5", "香蕉黃"),
    ("6", "橘子橘"),
    ("7", "孔雀藍"),
    ("8", "石墨灰"),
    ("9", "藍莓藍"),
    ("10", "羅勒綠"),
    ("11", "番茄紅"),
]
GCAL_HEX = {
    "1": "#7986cb", "2": "#33b679", "3": "#8e24aa", "4": "#e67c73", "5": "#f6bf26",
    "6": "#f4511e", "7": "#039be5", "8": "#616161", "9": "#3f51b5", "10": "#0b8043", "11": "#d50000",
}


class Client(models.Model):
    name = models.CharField("客戶", max_length=60, unique=True)
    gcal_color = models.CharField("日曆顏色", max_length=2, choices=GCAL_COLORS, blank=True)
    is_active = models.BooleanField("使用中", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = verbose_name_plural = "客戶"

    def __str__(self):
        return self.name

    @property
    def hex(self):
        return GCAL_HEX.get(self.gcal_color, "#8a8f98")


class RecurringTask(models.Model):
    LAST_DAY = 0
    DAY_CHOICES = [(d, f"每月 {d} 號") for d in range(1, 29)] + [(LAST_DAY, "每月月底")]

    title = models.CharField("事項", max_length=200)
    client = models.ForeignKey(Client, verbose_name="客戶", on_delete=models.PROTECT, related_name="recurring")
    day_of_month = models.PositiveSmallIntegerField("哪一天", choices=DAY_CHOICES, default=1)
    notes = models.TextField("備註", blank=True)
    active = models.BooleanField("啟用", default=True)
    start_date = models.DateField("從哪天開始", default=timezone.localdate)
    gcal_event_id = models.CharField(max_length=200, blank=True)
    sync_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["client__name", "day_of_month", "title"]
        verbose_name = verbose_name_plural = "每月循環"

    def __str__(self):
        return f"[{self.client}] {self.title}"

    @property
    def day_label(self):
        return "月底" if self.day_of_month == self.LAST_DAY else f"{self.day_of_month} 號"

    def date_in(self, year: int, month: int) -> date:
        last = calendar.monthrange(year, month)[1]
        day = last if self.day_of_month == self.LAST_DAY else min(self.day_of_month, last)
        return date(year, month, day)

    def first_occurrence(self) -> date:
        d = self.date_in(self.start_date.year, self.start_date.month)
        if d < self.start_date:
            y, m = (self.start_date.year + 1, 1) if self.start_date.month == 12 else (self.start_date.year, self.start_date.month + 1)
            d = self.date_in(y, m)
        return d

    @property
    def rrule(self) -> str:
        day = -1 if self.day_of_month == self.LAST_DAY else self.day_of_month
        return f"RRULE:FREQ=MONTHLY;BYMONTHDAY={day}"


class TaskQuerySet(models.QuerySet):
    def visible(self):
        return self.filter(archived=False)


class Task(models.Model):
    title = models.CharField("事項", max_length=200)
    client = models.ForeignKey(Client, verbose_name="客戶", on_delete=models.PROTECT, related_name="tasks", null=True, blank=True)
    due_date = models.DateField("日期", null=True, blank=True)
    notes = models.TextField("備註", blank=True)
    is_milestone = models.BooleanField("里程碑（上線日等）", default=False)
    done = models.BooleanField("完成", default=False)
    done_at = models.DateTimeField(null=True, blank=True)
    archived = models.BooleanField(default=False)  # 循環任務的單次刪除用，避免被重新產生
    recurring = models.ForeignKey(RecurringTask, on_delete=models.SET_NULL, null=True, blank=True, related_name="instances")
    gcal_event_id = models.CharField(max_length=200, blank=True)
    sync_error = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = TaskQuerySet.as_manager()

    class Meta:
        ordering = ["done", models.F("due_date").asc(nulls_last=True), "-is_milestone", "created_at"]
        verbose_name = verbose_name_plural = "待辦"
        constraints = [
            models.UniqueConstraint(
                fields=["recurring", "due_date"],
                condition=Q(recurring__isnull=False),
                name="one_instance_per_recurring_date",
            )
        ]

    def __str__(self):
        return self.title

    @property
    def syncs_to_calendar(self) -> bool:
        """循環任務的每一次由日曆上的循環系列事件代表，不另外建事件。"""
        return self.recurring_id is None
