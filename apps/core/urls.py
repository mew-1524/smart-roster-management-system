from django.urls import path

from . import views
from .crud import crud_urls

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("availability/", views.availability, name="availability"),
    path("availability/leaves/new/", views.leave_form, name="leave_new"),
    path("availability/leaves/<int:pk>/edit/", views.leave_form, name="leave_edit"),
    path("availability/leaves/<int:pk>/impact/", views.leave_impact, name="leave_impact"),
    path("availability/leaves/<int:pk>/apply/", views.leave_apply, name="leave_apply"),
    path("availability/leaves/<int:pk>/<str:action>/", views.leave_action, name="leave_action"),
    path("availability/compoffs/new/", views.compoff_form, name="compoff_new"),
    path("availability/compoffs/<int:pk>/edit/", views.compoff_form, name="compoff_edit"),
    path("availability/compoffs/<int:pk>/<str:action>/", views.compoff_action, name="compoff_action"),
    path("availability/weekly-offs/<int:pk>/", views.weekly_off_edit, name="weekly_off_edit"),
    path("settings/", views.settings_view, name="settings"),
    path("settings/demo/run/", views.demo_run, name="demo_run"),
    path("settings/demo/reset/", views.demo_reset, name="demo_reset"),
    path("search/", views.search, name="search"),
] + crud_urls()
