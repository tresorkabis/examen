import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from app.models import Grille, GrilleEtudiant, Etudiant, HistoriquePromotion
from django.db.models import Min, Max, F

g = Grille.objects.get(pk=49)
print('Grille 49 historique_promotions count:', g.parcours.count())

# The exact palmares filter
meilleures = (
    GrilleEtudiant.objects
    .filter(grille__parcours__isnull=False, etudiant__isnull=False)
    .filter(grille=g)
    .values('etudiant')
    .annotate(meilleur_rang=Min('rang'), meilleure_moyenne=Max('moyenne'))
    .order_by('meilleur_rang', '-meilleure_moyenne')
)
print('Palmares count for grille 49:', meilleures.count())
for m in meilleures:
    etu = Etudiant.objects.get(pk=m['etudiant'])
    print('  etudiant_id', m['etudiant'], '|', etu.noms, '| rang', m['meilleur_rang'], '| moy', m['meilleure_moyenne'])

# Rows excluded (etudiant_id=None)
print()
print('Rows excluded (etudiant_id=NULL):')
excl = GrilleEtudiant.objects.filter(grille=g, etudiant_id__isnull=True).order_by('rang')
for ge in excl:
    print('  rang', ge.rang, '| noms:', repr(ge.noms), '| moyenne:', ge.moyenne)
