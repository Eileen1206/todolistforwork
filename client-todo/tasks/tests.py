from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from . import gcal
from .models import Client, RecurringTask, Task
from .parser import parse
from .recurring import ensure_instances

TODAY = date(2026, 10, 21)

CAL_TODO = """CAL todo

10/20
- 6400 reimage upgrade(rm -rf /mnt/xxx first)
- 看3950要納管在哪台SMS
- impot 3950 object import to 54.36.
- 補貼3950設定於
    A > set interface eth1 link-speed 1000M/full
    B > set interface eth1 auto-negotiation on

10/xx
- meeting with benis for 10/28 online.

10/28(3950 & 9200 online)
- crontab(add cron job cpbackup command "/home/admin/auto_upgrade_export.sh" recurrence weekly days 0 time 2:30)



找時間stagging 9800並看線夠不夠長，做線標...等

11/7(9800 core fw)
- 9800上線當天備4條光纖
"""


class ParserTests(TestCase):
    def test_cal_todo_sample(self):
        r = parse(CAL_TODO, today=TODAY)
        self.assertEqual(r.clients, ["CAL"])
        got = [(i.due_date, i.title, i.is_milestone) for i in r.items]
        self.assertEqual(got, [
            (date(2026, 10, 20), "6400 reimage upgrade(rm -rf /mnt/xxx first)", False),
            (date(2026, 10, 20), "看3950要納管在哪台SMS", False),
            (date(2026, 10, 20), "impot 3950 object import to 54.36", False),
            (date(2026, 10, 20), "補貼3950設定於", False),
            (None, "meeting with benis for 10/28 online", False),
            (date(2026, 10, 28), "3950 & 9200 online", True),
            (date(2026, 10, 28), 'crontab(add cron job cpbackup command "/home/admin/auto_upgrade_export.sh" recurrence weekly days 0 time 2:30)', False),
            (None, "找時間stagging 9800並看線夠不夠長，做線標...等", False),
            (date(2026, 11, 7), "9800 core fw", True),
            (date(2026, 11, 7), "9800上線當天備4條光纖", False),
        ])
        self.assertTrue(all(i.client == "CAL" for i in r.items))
        self.assertEqual(r.items[3].notes, "A > set interface eth1 link-speed 1000M/full\nB > set interface eth1 auto-negotiation on")
        self.assertEqual(r.items[4].notes, "日期未定：10 月內")

    def test_wrapped_line_joins_title(self):
        r = parse("10/28\n- crontab(add cron job ... days 0 time\n2:30)", today=TODAY)
        self.assertEqual(len(r.items), 1)
        self.assertTrue(r.items[0].title.endswith("time 2:30)"))

    def test_inline_item_weekday_and_year_rollover(self):
        r = parse("10/30(五) 交報告\n1/5 年初盤點", today=date(2026, 12, 20))
        self.assertEqual([(i.due_date, i.title, i.is_milestone) for i in r.items], [
            (date(2026, 10, 30), "交報告", False),
            (date(2027, 1, 5), "年初盤點", False),
        ])

    def test_multiple_clients_and_plain_lines_under_date(self):
        r = parse("CAL todo\n10/22\n確認備份\n\nABC 待辦\n- 換 license", today=TODAY)
        self.assertEqual(r.clients, ["CAL", "ABC"])
        self.assertEqual([(i.client, i.due_date, i.title) for i in r.items], [
            ("CAL", date(2026, 10, 22), "確認備份"),
            ("ABC", None, "換 license"),
        ])

    def test_default_client_and_sub_bullets(self):
        r = parse("- 升級\n  - 先備份\n  - 再 reimage\n- 驗收", default_client="XYZ", today=TODAY)
        self.assertEqual([i.title for i in r.items], ["升級", "驗收"])
        self.assertEqual(r.items[0].notes, "先備份\n再 reimage")
        self.assertEqual(r.items[0].client, "XYZ")

    def test_invalid_date_kept_with_hint(self):
        r = parse("2/30\n- 不存在的日期", today=TODAY)
        self.assertIsNone(r.items[0].due_date)
        self.assertIn("日期待確認", r.items[0].notes)


class FlowTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("eileen", password="x" * 12)
        self.client.force_login(self.user)

    def test_login_required(self):
        self.client.logout()
        self.assertEqual(self.client.get("/").status_code, 302)

    def test_paste_preview_confirm(self):
        resp = self.client.post(reverse("paste"), {"text": CAL_TODO})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "建立 10 筆待辦")
        self.assertContains(resp, "會新增客戶：CAL")

        data = {"count": 10}
        for i, it in enumerate(resp.context["items"]):
            data.update({
                f"include-{i}": "on" if i != 7 else "",  # 取消勾選一筆
                f"title-{i}": it.title, f"client-{i}": it.client, f"notes-{i}": it.notes,
                f"date-{i}": it.due_date.isoformat() if it.due_date else "",
            } | ({f"milestone-{i}": "on"} if it.is_milestone else {}))
        resp = self.client.post(reverse("paste_confirm"), data)
        cal = Client.objects.get(name="CAL")
        self.assertRedirects(resp, f"/?client={cal.pk}", fetch_redirect_response=False)
        self.assertEqual(Task.objects.count(), 9)
        self.assertEqual(Task.objects.filter(is_milestone=True).count(), 2)

        resp = self.client.get(f"/?client={cal.pk}")
        self.assertContains(resp, "補貼3950設定於")
        self.assertContains(resp, "set interface eth1 auto-negotiation on")

    def test_quick_add_and_toggle(self):
        Client.objects.create(name="CAL")
        self.client.post(reverse("quick_add"), {"text": "10/30 交報告", "client": "cal"})
        t = Task.objects.get()
        self.assertEqual(t.client.name, "CAL")  # 不分大小寫對到既有客戶
        self.client.post(reverse("task_toggle", args=[t.pk]))
        t.refresh_from_db()
        self.assertTrue(t.done and t.done_at)

    def test_unsafe_next_ignored(self):
        t = Task.objects.create(title="x")
        resp = self.client.post(reverse("task_toggle", args=[t.pk]), {"next": "https://evil.example/"})
        self.assertEqual(resp["Location"], "/")

    def test_all_pages_render(self):
        c = Client.objects.create(name="CAL", gcal_color="7")
        rt = RecurringTask.objects.create(title="月報", client=c, day_of_month=5, start_date=date(2026, 1, 1))
        t = Task.objects.create(title="x", client=c, due_date=TODAY)
        for url in ["/", "/?client=none", f"/?client={c.pk}", "/paste/", "/task/new/", f"/task/{t.pk}/edit/",
                    "/recurring/", "/recurring/new/", f"/recurring/{rt.pk}/edit/", "/clients/", f"/clients/{c.pk}/edit/"]:
            self.assertEqual(self.client.get(url).status_code, 200, url)


class RecurringTests(TestCase):
    def setUp(self):
        self.c = Client.objects.create(name="CAL")

    def test_instances_this_and_next_month(self):
        RecurringTask.objects.create(title="月底備份檢查", client=self.c, day_of_month=0, start_date=date(2026, 10, 1))
        RecurringTask.objects.create(title="5 號報表", client=self.c, day_of_month=5, start_date=TODAY)
        ensure_instances(TODAY)
        ensure_instances(TODAY)  # 重複呼叫不會多建
        got = sorted((t.title, t.due_date) for t in Task.objects.all())
        self.assertEqual(got, [
            ("5 號報表", date(2026, 11, 5)),  # 10/5 在開始日前，不建
            ("月底備份檢查", date(2026, 10, 31)),
            ("月底備份檢查", date(2026, 11, 30)),
        ])

    def test_deleted_instance_not_regenerated(self):
        rt = RecurringTask.objects.create(title="報表", client=self.c, day_of_month=25, start_date=date(2026, 10, 1))
        ensure_instances(TODAY)
        Task.objects.filter(due_date=date(2026, 10, 25)).update(archived=True)
        ensure_instances(TODAY)
        self.assertEqual(Task.objects.visible().filter(recurring=rt).count(), 1)

    def test_rrule_and_first_occurrence(self):
        rt = RecurringTask(title="x", client=self.c, day_of_month=0, start_date=date(2027, 2, 10))
        self.assertEqual(rt.rrule, "RRULE:FREQ=MONTHLY;BYMONTHDAY=-1")
        self.assertEqual(rt.first_occurrence(), date(2027, 2, 28))
        rt.day_of_month = 5
        self.assertEqual(rt.first_occurrence(), date(2027, 3, 5))


# ---------- Google Calendar（用假的 service，不連網） ----------

class FakeRequest:
    def __init__(self, result=None, error=None):
        self.result, self.error = result, error

    def execute(self):
        if self.error:
            raise self.error
        return self.result


class FakeEvents:
    def __init__(self):
        self.calls, self.n = [], 0

    def insert(self, calendarId, body):
        self.n += 1
        self.calls.append(("insert", body))
        return FakeRequest({"id": f"ev{self.n}"})

    def patch(self, calendarId, eventId, body):
        self.calls.append(("patch", eventId, body))
        return FakeRequest({"id": eventId})

    def update(self, calendarId, eventId, body):
        self.calls.append(("update", eventId, body))
        return FakeRequest({"id": eventId})

    def delete(self, calendarId, eventId):
        self.calls.append(("delete", eventId))
        return FakeRequest({})


class FakeBatch:
    def __init__(self, callback):
        self.callback, self.reqs = callback, []

    def add(self, req, request_id):
        self.reqs.append((req, request_id))

    def execute(self):
        for req, rid in self.reqs:
            self.callback(rid, req.execute(), None)


class FakeService:
    def __init__(self):
        self._events = FakeEvents()

    def events(self):
        return self._events

    def new_batch_http_request(self, callback):
        return FakeBatch(callback)


@override_settings(GOOGLE_SERVICE_ACCOUNT_JSON="{}", GOOGLE_CALENDAR_ID="cal@group.calendar.google.com")
class GcalTests(TestCase):
    def setUp(self):
        self.svc = FakeService()
        p = mock.patch.object(gcal, "_service", return_value=self.svc)
        p.start()
        self.addCleanup(p.stop)
        self.c = Client.objects.create(name="CAL", gcal_color="7")

    def test_body(self):
        t = Task.objects.create(title="9800 core fw", client=self.c, due_date=date(2026, 11, 7), is_milestone=True, notes="備 4 條光纖")
        b = gcal.task_body(t)
        self.assertEqual(b["summary"], "★ [CAL] 9800 core fw")
        self.assertEqual(b["start"], {"date": "2026-11-07"})
        self.assertEqual(b["end"], {"date": "2026-11-08"})
        self.assertEqual(b["colorId"], "7")

    def test_lifecycle(self):
        t = Task.objects.create(title="a", client=self.c, due_date=TODAY)
        gcal.push_task(t)
        t.refresh_from_db()
        self.assertEqual(t.gcal_event_id, "ev1")
        t.done = True
        gcal.push_task(t)
        self.assertEqual(self.svc._events.calls[-1][0], "patch")
        self.assertTrue(self.svc._events.calls[-1][2]["summary"].startswith("✔"))
        t.due_date = None
        gcal.push_task(t)
        t.refresh_from_db()
        self.assertEqual(self.svc._events.calls[-1], ("delete", "ev1"))
        self.assertEqual(t.gcal_event_id, "")

    def test_push_many_batches_and_skips_undated_and_recurring(self):
        rt = RecurringTask.objects.create(title="r", client=self.c, day_of_month=1)
        tasks = [
            Task.objects.create(title="a", client=self.c, due_date=TODAY),
            Task.objects.create(title="b", client=self.c, due_date=None),
            Task.objects.create(title="c", client=self.c, due_date=TODAY, recurring=rt),
        ]
        self.assertEqual(gcal.push_many(tasks), 0)
        self.assertEqual([c[0] for c in self.svc._events.calls], ["insert"])
        self.assertEqual(Task.objects.exclude(gcal_event_id="").count(), 1)

    def test_failure_recorded_and_resync(self):
        from googleapiclient.errors import HttpError
        t = Task.objects.create(title="a", client=self.c, due_date=TODAY)
        with mock.patch.object(FakeEvents, "insert", return_value=FakeRequest(error=TimeoutError("timed out"))):
            gcal.push_task(t)
        t.refresh_from_db()
        self.assertIn("timed out", t.sync_error)

        user = get_user_model().objects.create_user("u", password="x" * 12)
        self.client.force_login(user)
        self.assertContains(self.client.get("/"), "1 筆還沒寫進 Google Calendar")
        self.client.post(reverse("resync"))
        t.refresh_from_db()
        self.assertEqual((t.sync_error, t.gcal_event_id), ("", "ev1"))

        # 事件被手動從日曆刪掉 → patch 404 時改成重建
        gone = HttpError(resp=mock.Mock(status=404), content=b"not found")
        with mock.patch.object(FakeEvents, "patch", return_value=FakeRequest(error=gone)):
            gcal.push_task(t)
        t.refresh_from_db()
        self.assertEqual(t.gcal_event_id, "ev2")

    def test_recurring_series(self):
        rt = RecurringTask.objects.create(title="月底備份檢查", client=self.c, day_of_month=0, start_date=TODAY)
        gcal.push_recurring(rt)
        kind, body = self.svc._events.calls[-1]
        self.assertEqual(kind, "insert")
        self.assertEqual(body["recurrence"], ["RRULE:FREQ=MONTHLY;BYMONTHDAY=-1"])
        self.assertEqual(body["start"], {"date": "2026-10-31"})
        gcal.push_recurring(rt, delete=True)
        self.assertEqual(self.svc._events.calls[-1], ("delete", "ev1"))
