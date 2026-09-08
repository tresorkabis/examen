from django.contrib import admin

from .models import (Promotion, Enseignant, Etudiant, Cours, Session,
                     Examen, Inscription)

admin.site.register([Promotion, Enseignant, Etudiant, Cours, Session,
                     Examen, Inscription])
