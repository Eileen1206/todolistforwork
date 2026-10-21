"""寫入 Google Calendar（service account + 一個共用給它的日曆）。

沒設定 GOOGLE_SERVICE_ACCOUNT_JSON / GOOGLE_CALENDAR_ID 時全部安靜跳過，網站照常可用。
所有呼叫都有逾時，Google 掛掉也不會把 gunicorn worker 卡死；失敗會記在 sync_error，
儀表板上按「重新同步」就能補。
"""

from __future__ import annotations

import json
import logging
from datetime import timedelta
from typing import Iterable

from django.conf import settings

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar.events"]
TIMEOUT_SECONDS = 10
BATCH_SIZE = 50


def enabled() -> bool:
    return bool(settings.GOOGLE_SERVICE_ACCOUNT_JSON and settings.GOOGLE_CALENDAR_ID)


def _service():
    import httplib2
    from google.oauth2 import service_account
    from google_auth_httplib2 import AuthorizedHttp
    from googleapiclient.discovery import build

    info = json.loads(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    http = AuthorizedHttp(creds, http=httplib2.Http(timeout=TIMEOUT_SECONDS))
    return build("calendar", "v3", http=http, cache_discovery=False)


def _is_gone(exc: Exception) -> bool:
    status = getattr(getattr(exc, "resp", None), "status", None)
    return status in (404, 410)


def _err(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"[:300]


def _site_link(path: str) -> str:
    base = settings.SITE_URL.rstrip("/")
    return f"\n\n{base}{path}" if base else ""


# ---------- 一般待辦 ----------

def task_body(task) -> dict:
    client = f"[{task.client.name}] " if task.client else ""
    mark = "✔ " if task.done else ("★ " if task.is_milestone else "")
    body = {
        "summary": f"{mark}{client}{task.title}",
        "description": (task.notes or "") + _site_link(f"/task/{task.pk}/edit/"),
        "start": {"date": task.due_date.isoformat()},
        "end": {"date": (task.due_date + timedelta(days=1)).isoformat()},
        "transparency": "transparent",  # 不佔用「忙碌」時段
        "extendedProperties": {"private": {"client_todo_task": str(task.pk)}},
    }
    color = task.client.gcal_color if task.client else ""
    if color:
        body["colorId"] = color
    return body


def push_task(task, service=None) -> None:
    """依待辦目前狀態新增／更新／刪除日曆事件，結果寫回 task。"""
    if not enabled() or not task.syncs_to_calendar:
        return
    cal = settings.GOOGLE_CALENDAR_ID
    try:
        service = service or _service()
        events = service.events()
        if task.archived or task.due_date is None:
            if task.gcal_event_id:
                try:
                    events.delete(calendarId=cal, eventId=task.gcal_event_id).execute()
                except Exception as exc:  # 已被手動刪掉就算了
                    if not _is_gone(exc):
                        raise
            task.gcal_event_id = ""
        elif task.gcal_event_id:
            try:
                events.patch(calendarId=cal, eventId=task.gcal_event_id, body=task_body(task)).execute()
            except Exception as exc:
                if not _is_gone(exc):
                    raise
                task.gcal_event_id = events.insert(calendarId=cal, body=task_body(task)).execute()["id"]
        else:
            task.gcal_event_id = events.insert(calendarId=cal, body=task_body(task)).execute()["id"]
        task.sync_error = ""
    except Exception as exc:
        log.exception("Google Calendar sync failed for task %s", task.pk)
        task.sync_error = _err(exc)
    type(task).objects.filter(pk=task.pk).update(gcal_event_id=task.gcal_event_id, sync_error=task.sync_error)


def push_many(tasks: Iterable) -> int:
    """大量貼上時用：還沒有事件的用 batch 一次送，其他逐筆處理。回傳失敗筆數。"""
    if not enabled():
        return 0
    tasks = [t for t in tasks if t.syncs_to_calendar]
    if not tasks:
        return 0
    try:
        service = _service()
    except Exception as exc:
        log.exception("Google Calendar auth failed")
        Model = type(tasks[0])
        Model.objects.filter(pk__in=[t.pk for t in tasks]).update(sync_error=_err(exc))
        return len(tasks)

    to_insert = [t for t in tasks if t.due_date and not t.archived and not t.gcal_event_id]
    others = [t for t in tasks if t not in to_insert]
    by_id = {str(t.pk): t for t in to_insert}

    def done(request_id, response, exception):
        t = by_id[request_id]
        if exception is not None:
            t.sync_error = _err(exception)
        else:
            t.gcal_event_id, t.sync_error = response["id"], ""

    cal = settings.GOOGLE_CALENDAR_ID
    for i in range(0, len(to_insert), BATCH_SIZE):
        chunk = to_insert[i : i + BATCH_SIZE]
        batch = service.new_batch_http_request(callback=done)
        for t in chunk:
            batch.add(service.events().insert(calendarId=cal, body=task_body(t)), request_id=str(t.pk))
        try:
            batch.execute()
        except Exception as exc:
            log.exception("Google Calendar batch insert failed")
            for t in chunk:
                if not t.gcal_event_id:
                    t.sync_error = _err(exc)
    for t in to_insert:
        type(t).objects.filter(pk=t.pk).update(gcal_event_id=t.gcal_event_id, sync_error=t.sync_error)

    for t in others:
        push_task(t, service=service)
    return sum(1 for t in tasks if t.sync_error)


# ---------- 每月循環 ----------

def recurring_body(rt) -> dict:
    first = rt.first_occurrence()
    body = {
        "summary": f"↻ [{rt.client.name}] {rt.title}",
        "description": (rt.notes or "") + _site_link("/recurring/"),
        "start": {"date": first.isoformat()},
        "end": {"date": (first + timedelta(days=1)).isoformat()},
        "recurrence": [rt.rrule],
        "transparency": "transparent",
        "extendedProperties": {"private": {"client_todo_recurring": str(rt.pk)}},
    }
    if rt.client.gcal_color:
        body["colorId"] = rt.client.gcal_color
    return body


def push_recurring(rt, *, delete: bool = False) -> None:
    if not enabled():
        return
    cal = settings.GOOGLE_CALENDAR_ID
    try:
        events = _service().events()
        if delete or not rt.active:
            if rt.gcal_event_id:
                try:
                    events.delete(calendarId=cal, eventId=rt.gcal_event_id).execute()
                except Exception as exc:
                    if not _is_gone(exc):
                        raise
            rt.gcal_event_id = ""
        elif rt.gcal_event_id:
            try:
                events.update(calendarId=cal, eventId=rt.gcal_event_id, body=recurring_body(rt)).execute()
            except Exception as exc:
                if not _is_gone(exc):
                    raise
                rt.gcal_event_id = events.insert(calendarId=cal, body=recurring_body(rt)).execute()["id"]
        else:
            rt.gcal_event_id = events.insert(calendarId=cal, body=recurring_body(rt)).execute()["id"]
        rt.sync_error = ""
    except Exception as exc:
        log.exception("Google Calendar sync failed for recurring %s", rt.pk)
        rt.sync_error = _err(exc)
    if not delete:
        type(rt).objects.filter(pk=rt.pk).update(gcal_event_id=rt.gcal_event_id, sync_error=rt.sync_error)
