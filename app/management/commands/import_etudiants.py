"""
Import des étudiants depuis les grilles de délibération Excel.

Les grilles (data/L*.xlsx) listent les élèves avec :
  - colonne 0 : N° d'ordre (numérique)
  - colonne 1 : NOMS complet au format « NOM POSTNOM PRÉNOM »
Les lignes suivantes (moyennes, signatures) n'ont pas de N° numérique.

Aucun matricule n'existe dans les grilles : un numéro d'étudiant est
généré de façon déterministe (ex. L1INFOA-001) pour rester idempotent.

Usage :
    python manage.py import_etudiants --dry-run   # simulation
    python manage.py import_etudiants             # import réel
"""
import re
import unicodedata
from pathlib import Path

import pandas as pd
from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Etudiant, Promotion

DATA_DIR = Path(__file__).resolve().parents[3] / 'data'

# Fichier -> nom de promotion (les 3 premiers existent déjà,
# créés par l'import de la charge horaire)
PROMO_MAP = {
    'L1 INFO LMD A_2025_2026.xlsx': 'L1 INFO A',
    'L1 SCF LMD 2SEM 2025_2026.xlsx': 'L1 SCF LMD',
    'L2 SCF_LMD 2SEM 2025_2026.xlsx': 'L2 SCF LMD',
    'L2 TS_LMD A 2025_2026.xlsx': 'L2 SDA',  # = « L2 SD A » de la charge horaire
    'L3 INFO LMD 2025_2026.xlsx': 'L3 INFO',
    'L3 SCF_LMD 2025_2026.xlsx': 'L3 SCF LMD',
    'L3 TS_LMD A 2025_2026.xlsx': 'L3 TS A',
}

NUM_RE = re.compile(r'^\d{1,3}$')


def slugify(text):
    """Minuscules sans accents ni espaces (pour matricules / emails)."""
    text = unicodedata.normalize('NFKD', text)
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]', '', text.lower())


class Command(BaseCommand):
    help = 'Importe les étudiants depuis les grilles de délibération (data/L*.xlsx)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Simule l\'import sans écrire en base')

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        created_total, updated_total = 0, 0

        if dry_run:
            self.stdout.write(self.style.WARNING('MODE SIMULATION (--dry-run)'))

        for filename, promo_name in PROMO_MAP.items():
            path = DATA_DIR / filename
            if not path.exists():
                self.stdout.write(self.style.ERROR(f'❌ {filename} introuvable, ignoré'))
                continue

            promotion, promo_created = Promotion.objects.get_or_create(nom=promo_name)
            if promo_created:
                self.stdout.write(self.style.NOTICE(f'➕ Promotion créée : {promo_name}'))

            df = pd.ExcelFile(path).parse(0, header=None)
            promo_slug = slugify(promo_name).upper()
            created_file, updated_file, seen_names = 0, 0, set()

            for i in range(df.shape[0]):
                num = str(df.iat[i, 0]).strip() if not pd.isna(df.iat[i, 0]) else ''
                full_name = str(df.iat[i, 1]).strip() if not pd.isna(df.iat[i, 1]) else ''
                if not NUM_RE.match(num) or not full_name:
                    continue  # en-têtes, totaux, signatures, cellules vides

                full_name = re.sub(r'\s+', ' ', full_name)
                if full_name.upper() in seen_names:
                    self.stdout.write(self.style.WARNING(
                        f'⚠️  Doublon dans {promo_name} : {full_name} (ligne {i + 1}), ignoré'))
                    continue
                seen_names.add(full_name.upper())

                # NOM POSTNOM PRÉNOM -> nom = 1er mot, prenom = reste
                parts = full_name.split(' ')
                nom = parts[0]
                prenom = ' '.join(parts[1:]) or '-'
                numero = f'{promo_slug}-{int(num):03d}'
                email = self._unique_email(nom, prenom)

                if dry_run:
                    created_file += 1
                    continue

                etu, was_created = Etudiant.objects.get_or_create(
                    numero_etudiant=numero,
                    defaults={'nom': nom, 'prenom': prenom, 'email': email,
                              'promotion': promotion},
                )
                if was_created:
                    created_file += 1
                else:
                    etu.nom, etu.prenom, etu.promotion = nom, prenom, promotion
                    etu.save()
                    updated_file += 1

            created_total += created_file
            updated_total += updated_file
            self.stdout.write(
                f'{filename} → {promo_name} : {created_file} créé(s)'
                + (f', {updated_file} mis à jour' if updated_file else ''))

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f'Simulation terminée : {created_total} étudiant(s) seraient importés'))
            transaction.set_rollback(True)
        else:
            self.stdout.write(self.style.SUCCESS(
                f'✨ Import terminé : {created_total} créé(s), {updated_total} mis à jour'))

    def _unique_email(self, nom, prenom):
        """Génère un email unique prenom.nom@example.com (suffixe si collision)."""
        base = f'{slugify(prenom)}.{slugify(nom)}' or 'etudiant'
        email, n = f'{base}@example.com', 2
        while Etudiant.objects.filter(email=email).exists():
            email = f'{base}{n}@example.com'
            n += 1
        return email
