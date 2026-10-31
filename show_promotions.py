import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Promotion, Mention, Filiere

print('=== Toutes les promotions avec Filière et Mention ===')
print('-' * 80)
for p in Promotion.objects.all().order_by('nom'):
    m = p.mention
    fil = m.filiere if m else None
    print('Promotion : %-30s' % str(p.nom))
    print('  Mention   : %-25s' % (str(m.nom) if m else '—'))
    print('  Filière   : %-25s' % (str(fil.nom) if fil else '—'))
    print('-' * 80)

print()
print('=== Résumé ===')
print('Nombre de promotions :', Promotion.objects.count())
print('Nombre de mentions :', Mention.objects.count())
print('Nombre de filières :', Filiere.objects.count())
print('Promotions avec mention :', Promotion.objects.exclude(mention=None).count())
print('Promotions sans mention :', Promotion.objects.filter(mention=None).count())