"""accounts/admin.py"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth import get_user_model

CustomUser = get_user_model()


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    """
    Enregistre le CustomUser dans l'admin.
    Le modèle hérite d'AbstractUser mais utilise l'email comme identifiant (USERNAME_FIELD='email').
    On garde le UserAdmin standard et on ajoute les champs perso.
    """

    # Liste dans /admin/accounts/customuser/
    list_display = (
        "email",
        "username",
        "first_name",
        "last_name",
        "is_staff",
        "is_active",
        "is_superuser",
    )
    search_fields = ("email", "username", "first_name", "last_name")
    ordering = ("email",)  # tri par email

    # On conserve les fieldsets par défaut et on ajoute tes champs custom
    fieldsets = UserAdmin.fieldsets + (
        (
            "Profil",
            {
                "fields": (
                    "date_of_birth",
                    "sexe",
                    "profile_picture_base64",
                    "bio",
                )
            },
        ),
    )

    # Le formulaire d’ajout utilise le USERNAME_FIELD du modèle (email),
    # on peut garder l’add_fieldsets par défaut.
    add_fieldsets = UserAdmin.add_fieldsets
