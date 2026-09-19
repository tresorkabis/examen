from django.contrib import admin

from .models import (Promotion, Enseignant, Etudiant, Cours, Session,
                     Examen, Inscription, Grille, GrilleUE, GrilleEtudiant,
                     GrilleNote)

admin.site.register([Promotion, Enseignant, Etudiant, Cours, Session,
                     Inscription, Grille, GrilleUE, GrilleEtudiant,
                     GrilleNote])


@admin.register(Examen)
class ExamenAdmin(admin.ModelAdmin):
    list_display = ('cours', 'session', 'date_examen', 'salle', 'est_note')
    list_filter = ('est_note', 'session', 'cours__promotion')
