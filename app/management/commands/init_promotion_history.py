"""Reconstitue le parcours académique des étudiants (années antérieures).

Un étudiant ne garde qu'une fiche : son parcours (L1 → L2 → L3) est conservé
dans `HistoriquePromotion`, année par année. Deux voies le renseignent
automatiquement :

* le **changement de promotion** (`Etudiant.save`) clôt l'étape précédente et
  ouvre la nouvelle ;
* l'**import d'une grille de délibération** date l'étape et la relie à la
  grille de l'année.

Cette commande rattrape ce qui précède le dispositif : elle crée l'étape
manquante de la promotion actuelle de chaque étudiant, puis complète les étapes
à partir des grilles déjà importées (année académique + grille). C'est elle qui
donne aux promotions de L2 et de L3 l'historique de leurs étudiants pour les
données déjà en base.

Idempotente : un second passage ne modifie plus rien.

Usage :
    python manage.py init_promotion_history --dry-run   # simulation
    python manage.py init_promotion_history             # écriture en base
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from app.models import Etudiant, GrilleEtudiant, HistoriquePromotion


def _etapes_incompletes():
    """Étapes sans année ou sans grille : la mesure du travail à faire."""
    return HistoriquePromotion.objects.filter(
        Q(annee_academique='') | Q(grille__isnull=True)).count()


class Command(BaseCommand):
    help = ("Reconstitue le parcours des étudiants : une étape par promotion "
            "et par année, complétée à partir des grilles importées.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', dest='dry_run', action='store_true',
            help='Simule la reconstitution sans rien écrire en base.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('MODE SIMULATION (--dry-run)'))

        with transaction.atomic():
            incompletes_avant = _etapes_incompletes()

            # 1. Étape manquante pour la promotion actuelle de l'étudiant.
            crees = 0
            for etudiant in Etudiant.objects.select_related('promotion'):
                if HistoriquePromotion.objects.filter(
                        etudiant=etudiant,
                        promotion=etudiant.promotion).exists():
                    continue
                if not dry_run:
                    HistoriquePromotion.enregistrer(
                        etudiant, etudiant.promotion)
                crees += 1

            # 2. Complète chaque étape depuis les grilles importées : cette
            #    passe renseigne l'année académique et la grille, et rend donc
            #    consultable le relevé de l'année antérieure.
            lignes_grille = 0
            for ligne in (GrilleEtudiant.objects
                          .select_related('grille', 'etudiant')):
                grille = ligne.grille
                if not dry_run:
                    HistoriquePromotion.enregistrer(
                        ligne.etudiant, grille.promotion,
                        grille.annee_academique, grille)
                lignes_grille += 1

            incompletes_apres = _etapes_incompletes()
            if dry_run:
                transaction.set_rollback(True)

        if dry_run:
            self.stdout.write(
                f'Simulation : {crees} étape(s) seraient créées et jusqu\'à '
                f'{incompletes_avant} étape(s) complétées depuis '
                f'{lignes_grille} ligne(s) de grille.')
            self.stdout.write(self.style.WARNING('Aucune écriture effectuée.'))
            return

        completees = max(incompletes_avant - incompletes_apres, 0)
        self.stdout.write(self.style.SUCCESS(
            f'Parcours reconstitué : {crees} étape(s) créée(s), '
            f'{completees} étape(s) complétée(s) depuis les grilles.'))
        self.stdout.write(
            f'Étapes restant sans année ni grille : {incompletes_apres}.')
