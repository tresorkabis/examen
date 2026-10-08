import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.test import RequestFactory
from app.views import PalmaresView
from django.db.models import Q

# 1) Check the exact db query with the full name
from app.models import GrilleEtudiant
qs = GrilleEtudiant.objects.filter(
    grille__parcours__isnull=False,
).filter(
    Q(etudiant__noms__icontains='BWAMBALE KAVUKULU SAMUEL') |
    Q(etudiant__numero_etudiant__icontains='BWAMBALE KAVUKULU SAMUEL') |
    Q(noms__icontains='BWAMBALE KAVUKULU SAMUEL'),
)
print('Count for full name:', qs.count())
for ge in qs:
    print('  noms:', repr(ge.noms), '| etudiant_id:', ge.etudiant_id)

# 2) Simulate the view with the full name URL (spaces as '+')
factory = RequestFactory()
request = factory.get('/palmares/?q=BWAMBALE+KAVUKULU+SAMUEL')
view = PalmaresView()
view.request = request
qs = view.get_queryset()
print('View count for full name:', len(qs))
for p in qs:
    print('  nom:', repr(p.get('nom')), '| etudiant_id:', p.get('etudiant_id'))
