from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from .models import (CotationProjetTutore, Promotion, Enseignant, Etudiant,
                     Cours, Session, Inscription, Grille, GrilleUE,
                     GrilleEtudiant, GrilleNote, UserProfile, Examen)

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "Profil utilisateur"
    fields = ("role", "enseignant")
    # Le rôle et le lien enseignant restent modifiables depuis l'admin Django.
    # La suppression du profil se fait par suppression de l'utilisateur entier.

try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass

def profil_role(obj):
    """Affiche le rôle de l'utilisateur dans la liste de l'admin."""
    try:
        return obj.profile.get_role_display()
    except UserProfile.DoesNotExist:
        return "—"


profil_role.short_description = "Rôle"


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)
    list_display = ("username", "email", "first_name", "last_name",
                    profil_role, "is_active", "is_staff")
    list_filter = ("is_active", "is_staff", "is_superuser",
                   "profile__role")
    search_fields = ("username", "email", "first_name", "last_name")
    ordering = ("username",)

@admin.register(Etudiant)
class EtudiantAdmin(admin.ModelAdmin):
    """Administration des étudiants.

    Le filtre « promotion » permet d'isoler les étudiants d'une promotion
    donnée puis de les supprimer en masse via l'action par défaut de
    l'administration (« Supprimer les étudiants sélectionnés »).
    """
    list_display = ("noms", "numero_etudiant", "promotion",
                    "date_naissance", "lieu_naissance")
    list_filter = ("promotion",)
    search_fields = ("noms", "numero_etudiant")
    ordering = ("noms",)


admin.site.register([Promotion, Enseignant, Cours, Session,
                     Inscription, Grille, GrilleUE, GrilleEtudiant, GrilleNote])

@admin.register(CotationProjetTutore)
class CotationProjetTutoreAdmin(admin.ModelAdmin):
    list_display = ("etudiant", "directeur", "lecteur1", "lecteur2",
                    "total_general", "mention")
    list_select_related = ("etudiant", "directeur", "lecteur1", "lecteur2")
    readonly_fields = ("total_general", "pourcentage", "mention",
                       "cases_renseignees")

@admin.register(Examen)
class ExamenAdmin(admin.ModelAdmin):
    list_display = ("cours", "session", "date_examen", "salle", "est_note")
    list_filter = ("est_note", "session", "cours__promotion")
