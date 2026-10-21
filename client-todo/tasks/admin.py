from django.contrib import admin

from .models import Client, RecurringTask, Task


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ["name", "gcal_color", "is_active"]


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ["title", "client", "due_date", "done", "is_milestone", "recurring", "sync_error"]
    list_filter = ["client", "done", "is_milestone"]
    search_fields = ["title", "notes"]


@admin.register(RecurringTask)
class RecurringTaskAdmin(admin.ModelAdmin):
    list_display = ["title", "client", "day_of_month", "active", "sync_error"]
