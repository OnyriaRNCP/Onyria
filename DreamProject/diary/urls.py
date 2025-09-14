from django.urls import path
from . import views

urlpatterns = [
    path('',views.home,name='home'),
    path('diary/', views.dream_diary_view, name='dream_diary'),
    path('record/', views.dream_recorder_view, name='dream_recorder'),
    path('transcribe/', views.transcribe, name='transcribe'),
    path('analyse_from_voice/', views.analyse_from_voice, name='analyse_from_voice'),
    path('followup/', views.dream_followup, name='dream_followup'),
    path('dream/<int:dream_id>/', views.dream_detail_view, name='dream_detail'),
]