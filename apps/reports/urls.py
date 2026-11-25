from django.urls import path

from . import views

urlpatterns = [
    path("", views.index, name="reports"),
    path("employee/", views.employee_report, name="report_employee"),
    path("brand/", views.brand_report, name="report_brand"),
    path("pod/", views.pod_report, name="report_pod"),
    path("conflicts/", views.conflict_report, name="report_conflicts"),
]
