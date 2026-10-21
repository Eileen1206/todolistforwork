from datetime import date, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import gcal
from .forms import ClientForm, PasteForm, RecurringForm, TaskForm
from .models import Client, RecurringTask, Task
from .parser import parse
from .recurring import ensure_instances, refresh_future_instances


def _safe_next(request) -> str:
    nxt = request.POST.get("next") or request.GET.get("next") or ""
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return nxt
    return ""


def _back(request, fallback="dashboard"):
    return redirect(_safe_next(request) or fallback)


def _client_by_name(name: str) -> Client | None:
    name = (name or "").strip()
    if not name:
        return None
    existing = Client.objects.filter(name__iexact=name).first()
    return existing or Client.objects.create(name=name)


def _report_sync(request, failed: int) -> None:
    if failed:
        messages.warning(request, f"有 {failed} 筆沒寫進 Google Calendar，稍後按「重新同步」即可補上。")


# ---------- 儀表板 ----------

@login_required
def dashboard(request):
    today = timezone.localdate()
    ensure_instances(today)

    open_filter = Q(tasks__done=False, tasks__archived=False)
    clients = Client.objects.filter(is_active=True).annotate(open_count=Count("tasks", filter=open_filter))
    no_client_count = Task.objects.visible().filter(done=False, client__isnull=True).count()

    selected = request.GET.get("client", "")
    qs = Task.objects.visible().select_related("client", "recurring")
    selected_client = None
    if selected == "none":
        qs = qs.filter(client__isnull=True)
    elif selected.isdigit():
        selected_client = Client.objects.filter(pk=selected).first()
        qs = qs.filter(client_id=selected)

    open_ = qs.filter(done=False)
    week_end = today + timedelta(days=7)
    sections = [
        ("overdue", "逾期", open_.filter(due_date__lt=today)),
        ("today", "今天", open_.filter(due_date=today)),
        ("week", "7 天內", open_.filter(due_date__gt=today, due_date__lte=week_end)),
        ("later", "之後", open_.filter(due_date__gt=week_end)),
        ("undated", "未排日期", open_.filter(due_date__isnull=True)),
    ]
    sections = [(key, label, list(items)) for key, label, items in sections]
    done_items = list(qs.filter(done=True).order_by("-done_at", "-updated_at")[:15])

    sync_pending = (
        Task.objects.visible().filter(recurring__isnull=True)
        .filter(Q(sync_error__gt="") | Q(due_date__isnull=False, gcal_event_id=""))
        .count()
        + RecurringTask.objects.filter(active=True).filter(Q(sync_error__gt="") | Q(gcal_event_id="")).count()
    ) if gcal.enabled() else 0

    return render(request, "tasks/dashboard.html", {
        "today": today,
        "clients": clients,
        "no_client_count": no_client_count,
        "selected": selected,
        "selected_client": selected_client,
        "sections": sections,
        "open_total": sum(len(s[2]) for s in sections),
        "open_total_all": Task.objects.visible().filter(done=False).count(),
        "done_items": done_items,
        "gcal_enabled": gcal.enabled(),
        "sync_pending": sync_pending,
    })


@login_required
@require_POST
def quick_add(request):
    text = request.POST.get("text", "").strip()
    client_name = request.POST.get("client", "")
    if not text:
        return _back(request)
    result = parse(text, default_client=client_name, today=timezone.localdate())
    created = _create_items([
        {"title": it.title, "due_date": it.due_date, "client": it.client, "notes": it.notes, "is_milestone": it.is_milestone}
        for it in result.items
    ])
    if created:
        _report_sync(request, gcal.push_many(created))
        messages.success(request, f"已新增 {len(created)} 筆。")
    return _back(request)


# ---------- 貼上 → 預覽 → 建立 ----------

@login_required
def paste(request):
    form = PasteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        result = parse(form.cleaned_data["text"], default_client=form.cleaned_data["default_client"],
                       today=timezone.localdate())
        if not result.items:
            messages.warning(request, "沒有拆出任何事項，檢查一下格式？")
        else:
            return render(request, "tasks/preview.html", {
                "items": result.items,
                "client_names": sorted({*Client.objects.values_list("name", flat=True), *result.clients}),
                "new_clients": [c for c in result.clients if not Client.objects.filter(name__iexact=c).exists()],
            })
    return render(request, "tasks/paste.html", {"form": form, "client_names": Client.objects.values_list("name", flat=True)})


@login_required
@require_POST
def paste_confirm(request):
    rows = []
    count = int(request.POST.get("count", 0))
    for i in range(count):
        if not request.POST.get(f"include-{i}"):
            continue
        title = request.POST.get(f"title-{i}", "").strip()
        if not title:
            continue
        raw_date = request.POST.get(f"date-{i}", "").strip()
        try:
            due = date.fromisoformat(raw_date) if raw_date else None
        except ValueError:
            due = None
        rows.append({
            "title": title[:200],
            "due_date": due,
            "client": request.POST.get(f"client-{i}", ""),
            "notes": request.POST.get(f"notes-{i}", "").strip(),
            "is_milestone": bool(request.POST.get(f"milestone-{i}")),
        })
    created = _create_items(rows)
    _report_sync(request, gcal.push_many(created))
    messages.success(request, f"已建立 {len(created)} 筆待辦。")
    clients = {t.client_id for t in created}
    if len(clients) == 1 and None not in clients:
        return redirect(f"{reverse('dashboard')}?client={clients.pop()}")
    return redirect("dashboard")


def _create_items(rows) -> list[Task]:
    cache: dict[str, Client | None] = {}
    created = []
    with transaction.atomic():
        for r in rows:
            key = r["client"].strip().lower()
            if key not in cache:
                cache[key] = _client_by_name(r["client"])
            created.append(Task.objects.create(
                title=r["title"], due_date=r["due_date"], client=cache[key],
                notes=r["notes"], is_milestone=r["is_milestone"],
            ))
    return created


# ---------- 單筆待辦 ----------

@login_required
@require_POST
def toggle_done(request, pk):
    task = get_object_or_404(Task, pk=pk)
    task.done = not task.done
    task.done_at = timezone.now() if task.done else None
    task.save(update_fields=["done", "done_at", "updated_at"])
    gcal.push_task(task)
    return _back(request)


@login_required
def task_edit(request, pk=None):
    task = get_object_or_404(Task, pk=pk) if pk else None
    initial = {}
    if task is None and request.GET.get("client", "").isdigit():
        initial["client"] = request.GET["client"]
    form = TaskForm(request.POST or None, instance=task, initial=initial)
    if request.method == "POST" and form.is_valid():
        was_done = task.done if task else False
        obj = form.save(commit=False)
        if obj.done and not was_done:
            obj.done_at = timezone.now()
        elif not obj.done:
            obj.done_at = None
        obj.save()
        gcal.push_task(obj)
        if obj.recurring_id:
            messages.info(request, "這是每月循環的其中一次：只改這一次，日曆上的循環事件不變。")
        messages.success(request, "已儲存。")
        return _back(request)
    return render(request, "tasks/task_form.html", {"form": form, "task": task, "back": _safe_next(request) or reverse("dashboard")})


@login_required
@require_POST
def task_delete(request, pk):
    task = get_object_or_404(Task, pk=pk)
    task.archived = True
    task.save(update_fields=["archived", "updated_at"])
    gcal.push_task(task)  # 刪掉日曆事件
    if task.recurring_id is None:
        task.delete()
    messages.success(request, f"已刪除「{task.title}」。")
    return _back(request)


@login_required
@require_POST
def resync(request):
    tasks = list(
        Task.objects.visible().filter(recurring__isnull=True)
        .filter(Q(sync_error__gt="") | Q(due_date__isnull=False, gcal_event_id=""))
        .select_related("client")
    )
    failed = gcal.push_many(tasks)
    for rt in RecurringTask.objects.filter(active=True).filter(Q(sync_error__gt="") | Q(gcal_event_id="")).select_related("client"):
        gcal.push_recurring(rt)
        failed += bool(rt.sync_error)
    if failed:
        messages.warning(request, f"還有 {failed} 筆失敗，錯誤訊息在該筆的編輯頁。")
    else:
        messages.success(request, "Google Calendar 已同步。")
    return _back(request)


# ---------- 每月循環 ----------

@login_required
def recurring_list(request):
    items = RecurringTask.objects.select_related("client")
    return render(request, "tasks/recurring_list.html", {"items": items, "has_clients": Client.objects.exists()})


@login_required
def recurring_edit(request, pk=None):
    rt = get_object_or_404(RecurringTask, pk=pk) if pk else None
    form = RecurringForm(request.POST or None, instance=rt)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        gcal.push_recurring(obj)
        refresh_future_instances(obj)
        messages.success(request, "已儲存每月循環。")
        return redirect("recurring_list")
    return render(request, "tasks/recurring_form.html", {"form": form, "rt": rt})


@login_required
@require_POST
def recurring_delete(request, pk):
    rt = get_object_or_404(RecurringTask, pk=pk)
    gcal.push_recurring(rt, delete=True)
    Task.objects.filter(recurring=rt, done=False).delete()
    title = rt.title
    rt.delete()
    messages.success(request, f"已刪除每月循環「{title}」，之後不會再產生。")
    return redirect("recurring_list")


# ---------- 客戶 ----------

@login_required
def client_list(request):
    form = ClientForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "已新增客戶。")
        return redirect("client_list")
    clients = Client.objects.annotate(open_count=Count("tasks", filter=Q(tasks__done=False, tasks__archived=False)))
    return render(request, "tasks/client_list.html", {"form": form, "clients": clients})


@login_required
def client_edit(request, pk):
    client = get_object_or_404(Client, pk=pk)
    old_color, old_name = client.gcal_color, client.name
    form = ClientForm(request.POST or None, instance=client)
    if request.method == "POST" and form.is_valid():
        client = form.save()
        if client.gcal_color != old_color or client.name != old_name:
            # 顏色或名稱變了 → 日曆上已存在的事件一起更新
            tasks = list(client.tasks.visible().filter(recurring__isnull=True).exclude(gcal_event_id="").select_related("client"))
            _report_sync(request, gcal.push_many(tasks))
            for rt in client.recurring.filter(active=True).select_related("client"):
                gcal.push_recurring(rt)
        messages.success(request, "已儲存。")
        return redirect("client_list")
    return render(request, "tasks/client_form.html", {"form": form, "client": client})
