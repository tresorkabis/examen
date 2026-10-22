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
    readonly_fields = ("role", "enseignant")

try:
    admin.site.unregister(User)
except admin.sites.NotRegistered:
    pass

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)

admin.site.register([Promotion, Enseignant, Etudiant, Cours, Session,
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
