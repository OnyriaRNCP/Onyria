from django.urls import path
from . import views
from .metrics.views import ai_health_view

urlpatterns = [
    path('', views.dream_diary_view, name='dream_diary'),
    path('record/', views.dream_recorder_view, name='dream_recorder'),
    path('analyse_from_voice/', views.analyse_from_voice, name='analyse_from_voice'),
    path('followup/', views.dream_followup, name='dream_followup'),
    path('dream/<int:dream_id>/', views.dream_detail_view, name='dream_detail'),
    path('delete/<int:dream_id>/', views.delete_dream, name='delete_dream'),
    
    # Santé IA (JSON)
    path('ai/health/', ai_health_view, name='ai_health'),
]