import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Filiere, Mention, Promotion

# --- Création des filières ---
lsi, _ = Filiere.objects.get_or_create(nom='Licence en Sciences Informatiques (LSI)')
lts, _ = Filiere.objects.get_or_create(nom='Licence en Techniques de Secretariat (LTS)')
lcf, _ = Filiere.objects.get_or_create(nom='Licence en Comptabilite et Finance (LCF)')

# --- Création des mentions ---
mention_genie, _ = Mention.objects.get_or_create(nom='Genie Logiciel', filiere=lsi)
mention_sysinfo, _ = Mention.objects.get_or_create(nom='Systemes Informatiques', filiere=lsi)
mention_secretariat, _ = Mention.objects.get_or_create(nom='Techniques de Secretariat', filiere=lts)
mention_compta, _ = Mention.objects.get_or_create(nom='Comptabilite et Finance', filiere=lcf)

# --- Tableau de correspondance promotion -> mention ---
mapping = {
    'INFO': mention_genie,
    'RTM': mention_sysinfo,
    'SD': mention_secretariat,
    'TS': mention_secretariat,
    'SCF': mention_compta,
    'SCA': mention_compta,
}

# --- Mise à jour des promotions existantes ---
for promo in Promotion.objects.all():
    nom = promo.nom.upper()
    matched = False
    for keyword, mention in mapping.items():
        if keyword in nom:
            promo.mention = mention
            promo.save(update_fields=['mention'])
            print('  %s -> %s' % (promo.nom, mention.nom))
            matched = True
            break
    if not matched:
        print('  %s -> (aucune correspondance)' % promo.nom)

print()
print('Filières :', Filiere.objects.count())
print('Mentions :', Mention.objects.count())
print('Promotions avec mention :', Promotion.objects.exclude(mention=None).count())