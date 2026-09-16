"""Fusionne les enseignants en double (même noms normalisé).

Règle : un seul survivant par personne (le plus petit id avec des cours,
sinon le plus petit id). Les cours des doublons sont réassignés au
survivant avant suppression.

Usage :
    python manage.py dedup_enseignants            # réel
    python manage.py dedup_enseignants --dry-run  # simulation
"""
import re
import unicodedata
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from app.models import Cours, Enseignant


def _norm(s):
    s = unicodedata.normalize('NFKD', (s or '').strip()).upper()
    s = ''.join(c for c in s if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', s)


class Command(BaseCommand):
    help = 'Fusionne les enseignants en double (même noms).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help="Simule sans écrire en base")

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('MODE SIMULATION (--dry-run)'))

        groupes = defaultdict(list)
        for e in Enseignant.objects.all().order_by('id'):
            groupes[_norm(e.noms)].append(e)

        fusionnes = 0
        cours_deplaces = 0
        for noms_norm, membres in sorted(groupes.items()):
            if len(membres) < 2:
                continue
            avec_cours = [e for e in membres
                          if Cours.objects.filter(enseignant=e).exists()]
            survivant = min(avec_cours or membres, key=lambda e: e.id)
            autres = [e for e in membres if e.id != survivant.id]
            nb = Cours.objects.filter(enseignant__in=autres).count()
            if dry_run:
                self.stdout.write(
                    f'  {survivant.noms} : x{len(membres)} -> garder '
                    f'id={survivant.id}, {nb} cours à rattacher, '
                    f'{len(autres)} à supprimer')
            else:
                Cours.objects.filter(enseignant__in=autres).update(
                    enseignant=survivant)
                Enseignant.objects.filter(id__in=[e.id for e in autres]).delete()
            fusionnes += len(autres)
            cours_deplaces += nb

        self.stdout.write(self.style.SUCCESS(
            f'{"Simulés" if dry_run else "Supprimés"} : {fusionnes} doublon(s), '
            f'{cours_deplaces} cours rattaché(s). '
            f'Reste : {len(groupes)} enseignant(s).'))

        if dry_run:
            transaction.set_rollback(True)
