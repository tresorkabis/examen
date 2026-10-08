import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.template.loader import render_to_string
ctx = {'title': 'Palmares des Étudiants', 'palmares': [
    {'rang_global': 1, 'nom': 'Dupont', 'numero': '12345', 'meilleure_moyenne': '15.678', 'meilleur_rang': 3, 'etudiant_id': 1},
    {'rang_global': 2, 'nom': 'Martin', 'numero': '67890', 'meilleure_moyenne': '0', 'meilleur_rang': 1, 'etudiant_id': 2},
]}
out = render_to_string('app/palmares.html', ctx)
print(out)
print('OK')
