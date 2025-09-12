from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.shortcuts import redirect

urlpatterns = [
    path("", lambda request: redirect("/accounts/login/"), name="root"),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('diary/', include('diary.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
