from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("quick-add/", views.quick_add, name="quick_add"),
    path("paste/", views.paste, name="paste"),
    path("paste/confirm/", views.paste_confirm, name="paste_confirm"),
    path("task/new/", views.task_edit, name="task_new"),
    path("task/<int:pk>/edit/", views.task_edit, name="task_edit"),
    path("task/<int:pk>/toggle/", views.toggle_done, name="task_toggle"),
    path("task/<int:pk>/delete/", views.task_delete, name="task_delete"),
    path("sync/", views.resync, name="resync"),
    path("recurring/", views.recurring_list, name="recurring_list"),
    path("recurring/new/", views.recurring_edit, name="recurring_new"),
    path("recurring/<int:pk>/edit/", views.recurring_edit, name="recurring_edit"),
    path("recurring/<int:pk>/delete/", views.recurring_delete, name="recurring_delete"),
    path("clients/", views.client_list, name="client_list"),
    path("clients/<int:pk>/edit/", views.client_edit, name="client_edit"),
]
