import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from app.models import Grille, GrilleEtudiant
from django.db import connection

g = Grille.objects.get(pk=49)

# The exact palmares filter
meilleures = (
    GrilleEtudiant.objects
    .filter(grille__parcours__isnull=False, etudiant__isnull=False)
    .filter(grille=g)
)

print('Count:', meilleures.count())
print('--- SQL ---')
print(meilleres.query if hasattr(meilleres, 'query') else 'n/a')
print('--- actual SQL (debug) ---')
print(meilleres.query.__str__())
