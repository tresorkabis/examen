import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Grille, Filiere, Mention, Promotion

grilles = Grille.objects.filter(promotion__nom="L2 SCF LMD")
print("Grilles L2 SCF LMD:", grilles.count())
for g in grilles:
    print(g)
    print("  total_credits:", g.total_credits)
    print("  bareme:", g.bareme)