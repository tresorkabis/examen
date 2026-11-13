import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Etudiant, Inscription

etudiant = Etudiant.objects.get(noms="BASAFULA KUTABIKISA ARLETTE")
inscriptions = etudiant.inscriptions.all()
print("Inscriptions count:", inscriptions.count())
for i in inscriptions:
    print(f"Examen: {i.examen.session}, note_interro: {i.note_interro}, note_tp: {i.note_tp}, note_examen: {i.note_examen}, note: {i.note}")