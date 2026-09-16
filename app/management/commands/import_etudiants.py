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

from app.excel_import import ReferentielImport, _numero_ordre
from app.models import Etudiant

DATA_DIR = Path(__file__).resolve().parents[3] / 'data'

# Fichier -> nom de promotion (les 3 premiers existent déjà,
# créés par l'import de la charge horaire)
PROMO_MAP = {
    'L1 INFO LMD A_2025_2026.xlsx': 'L1 INFO A',
    'L1 SCF LMD 2SEM 2025_2026.xlsx': 'L1 SCF LMD',
    'L2 SCF_LMD 2SEM 2025_2026.xlsx': 'L2 SCF LMD',
    'L2 TS_LMD A 2025_2026.xlsx': 'L2 SDA',  # = « L2 SD A » de la charge horaire
    'L3 INFO LMD 2025_2026.xlsx': 'L3 INFO A',
    'L3 SCF_LMD 2025_2026.xlsx': 'L3 SCF LMD',
    'L3 TS_LMD A 2025_2026.xlsx': 'L3 SDA',  # = « L3 SD A » de la charge horaire
}


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
        created_total, updated_total, unchanged_total = 0, 0, 0

        if dry_run:
            self.stdout.write(self.style.WARNING('MODE SIMULATION (--dry-run)'))

        # Cache des référentiels : évite ~4 requêtes SQL par étudiant, y compris
        # en mode simulation (où il rend l'aperçu quasi instantané).
        referentiel = ReferentielImport()

        for filename, promo_name in PROMO_MAP.items():
            path = DATA_DIR / filename
            if not path.exists():
                self.stdout.write(self.style.ERROR(f'❌ {filename} introuvable, ignoré'))
                continue

            promo_existait = promo_name in referentiel.promotions
            promotion = referentiel.promotion(promo_name)
            if not promo_existait:
                self.stdout.write(self.style.NOTICE(f'➕ Promotion créée : {promo_name}'))

            df = pd.ExcelFile(path).parse(0, header=None)
            promo_slug = slugify(promo_name).upper()
            created_file, updated_file, unchanged_file = 0, 0, 0
            seen_names = set()

            for i in range(df.shape[0]):
                rang = _numero_ordre(df.iat[i, 0])
                full_name = str(df.iat[i, 1]).strip() if not pd.isna(df.iat[i, 1]) else ''
                if rang is None or not 0 < rang < 1000 or not full_name:
                    continue  # en-têtes, totaux, signatures, cellules vides

                full_name = re.sub(r'\s+', ' ', full_name)
                if full_name.upper() in seen_names:
                    self.stdout.write(self.style.WARNING(
                        f'⚠️  Doublon dans {promo_name} : {full_name} (ligne {i + 1}), ignoré'))
                    continue
                seen_names.add(full_name.upper())

                noms_val = full_name
                numero = f'{promo_slug}-{rang:03d}'

                if dry_run:
                    # Le cache répond sans aucune requête SQL.
                    if referentiel.etudiant(promotion, numero=numero) is None:
                        created_file += 1
                    else:
                        updated_file += 1
                    continue

                etu = referentiel.etudiant(promotion, numero=numero)
                if etu is None:
                    etu = Etudiant.objects.create(
                        numero_etudiant=numero, noms=noms_val,
                        promotion=promotion)
                    referentiel.ajouter_etudiant(etu)
                    created_file += 1
                elif referentiel.maj_etudiant(etu, noms_val, promotion):
                    updated_file += 1
                else:
                    unchanged_file += 1


            created_total += created_file
            updated_total += updated_file
            unchanged_total += unchanged_file
            resume = f'{created_file} créé(s)'
            if updated_file:
                resume += f', {updated_file} mis à jour'
            if unchanged_file:
                resume += f', {unchanged_file} inchangé(s)'
            self.stdout.write(f'{filename} → {promo_name} : {resume}')

        if dry_run:
            self.stdout.write(self.style.SUCCESS(
                f'Simulation terminée : {created_total} étudiant(s) seraient '
                f'importés, {updated_total} mis à jour'))
            transaction.set_rollback(True)
        else:
            self.stdout.write(self.style.SUCCESS(
                f'✨ Import terminé : {created_total} créé(s), '
                f'{updated_total} mis à jour, {unchanged_total} inchangé(s)'))
