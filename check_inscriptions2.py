import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Etudiant, Inscription

etudiant = Etudiant.objects.get(noms="BASAFULA KUTABIKISA ARLETTE")
inscriptions = etudiant.inscriptions.all()
print("All inscriptions:")
for i in inscriptions:
    session = i.examen.session
    type_str = session.get_type_session_display()
    print(f"  Examen: {i.examen.session}, type: {type_str}, note_interro: {i.note_interro}, note_tp: {i.note_tp}, note_examen: {i.note_examen}, note: {i.note}")