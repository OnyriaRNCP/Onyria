"""
Django module for different redirections of the app.
Defines the mapping of navigation
"""

from django.urls import path
from . import views
from .metrics import views as metrics_views

urlpatterns = [
    path("", views.dream_diary_view, name="dream_diary"),
    path("record/", views.dream_recorder_view, name="dream_recorder"),
    path(
        "analyse_from_voice/",
        views.analyse_from_voice,
        name="analyse_from_voice",
    ),
    path("followup/", views.dream_followup, name="dream_followup"),
    path(
        "dream/<int:dream_id>/", views.dream_detail_view, name="dream_detail"
    ),
    path("delete/<int:dream_id>/", views.delete_dream, name="delete_dream"),
    # Santé IA (JSON)
    path("ai/health/", metrics_views.ai_health_view, name="ai_health"),
    path(
        "ai/health/download/",
        metrics_views.ai_health_download,
        name="ai_health_download",
    ),
]
