from django.contrib import admin

from .models import (Promotion, Enseignant, Etudiant, Cours, Session,
                     Examen, Inscription, Grille, GrilleUE, GrilleEtudiant,
                     GrilleNote, CotationProjetTutore)

admin.site.register([Promotion, Enseignant, Etudiant, Cours, Session,
                     Inscription, Grille, GrilleUE, GrilleEtudiant,
                     GrilleNote])


@admin.register(CotationProjetTutore)
class CotationProjetTutoreAdmin(admin.ModelAdmin):
    """Fiche de cotation : jury affecté + synthèse, en lecture seule."""

    list_display = ('etudiant', 'directeur', 'lecteur1', 'lecteur2',
                    'total_general', 'mention')
    list_select_related = ('etudiant', 'directeur', 'lecteur1', 'lecteur2')
    readonly_fields = ('total_general', 'pourcentage', 'mention',
                       'cases_renseignees')


@admin.register(Examen)
class ExamenAdmin(admin.ModelAdmin):
    list_display = ('cours', 'session', 'date_examen', 'salle', 'est_note')
    list_filter = ('est_note', 'session', 'cours__promotion')
