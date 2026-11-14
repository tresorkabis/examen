import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Promotion, Grille

p = Promotion.objects.get(pk=71)
g = Grille.objects.get(pk=9)

print("Promotion:", p)
print("Grille:", g)
print("Annee:", g.annee_academique)
print("total_credits:", g.total_credits)
print("Étudiants dans la grille:", g.lignes.count())