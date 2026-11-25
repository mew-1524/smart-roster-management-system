from django.urls import path

from . import views

urlpatterns = [
    path("", views.roster_home, name="roster_home"),
    path("generate/", views.generate_page, name="generate"),
    path("validate/", views.validate_action, name="validate"),
    path("publish/", views.publish_action, name="publish"),
    path("export/<str:fmt>/", views.roster_export, name="roster_export"),
    path("entry/<int:emp_pk>/<str:day>/", views.entry_edit, name="entry_edit"),
    path("conflicts/", views.conflicts_page, name="conflicts"),
    path("conflicts/<int:pk>/", views.conflict_detail, name="conflict_detail"),
    path("conflicts/<int:pk>/acknowledge/", views.conflict_ack, name="conflict_ack"),
    path("conflicts/<int:pk>/assign/", views.conflict_assign, name="conflict_assign"),
]
