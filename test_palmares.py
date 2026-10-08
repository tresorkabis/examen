import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from django.test import RequestFactory
from app.views import PalmaresView

factory = RequestFactory()

# Test 1: sans recherche
request = factory.get('/palmares/')
view = PalmaresView()
view.request = request
qs = view.get_queryset()
print('Test 1 - pas de recherche, nb items :', len(qs))
for p in qs[:3]:
    print('  -', p['nom'], '|', p['numero'], '| moy:', p['meilleure_moyenne'])

# Test 2: recherche par nom
request = factory.get('/palmares/?q=Martin')
view = PalmaresView()
view.request = request
qs = view.get_queryset()
print('Test 2 - recherche "Martin", nb items :', len(qs))
for p in qs:
    print('  -', p['nom'], '|', p['numero'], '| moy:', p['meilleure_moyenne'])

# Test 3: recherche par numéro
request = factory.get('/palmares/?q=12345')
view = PalmaresView()
view.request = request
qs = view.get_queryset()
print('Test 3 - recherche "12345", nb items :', len(qs))
for p in qs:
    print('  -', p['nom'], '|', p['numero'], '| moy:', p['meilleure_moyenne'])

# Test 4: recherche vide
request = factory.get('/palmares/?q=')
view = PalmaresView()
view.request = request
qs = view.get_queryset()
print('Test 4 - recherche vide, nb items :', len(qs))

print('OK')
