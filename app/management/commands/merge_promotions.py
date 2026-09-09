"""
Fusion de plusieurs promotions en une seule (étudiants + cours regroupés).

Utile quand une même classe porte des noms différents selon la source :
le fichier de charge horaire l'appelle « L2 SD A » alors que la grille de
délibération l'appelle « L2 TS LMD A » / « L2 TS A » (cf. promotion « L2 SDA »).

Les étudiants et les cours des promotions sources sont ré-affectés à la
promotion cible (créée si besoin), puis les sources — devenues vides —
sont supprimées. Les examens / inscriptions suivent automatiquement.

Usage :
    python manage.py merge_promotions --target "L2 SDA" --from "L2 SD A" "L2 TS A"
    python manage.py merge_promotions --target "L2 SDA" --from "L2 SD A" "L2 TS A" --dry-run
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import Cours, Etudiant, Promotion


class Command(BaseCommand):
    help = 'Fusionne plusieurs promotions en une seule (étudiants + cours).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--target', required=True,
            help='Nom de la promotion cible (créée si absente).')
        parser.add_argument(
            '--from', dest='sources', nargs='+', required=True,
            help='Noms des promotions sources à fusionner.')
        parser.add_argument(
            '--dry-run', dest='dry_run', action='store_true',
            help='Simule la fusion sans rien écrire en base.')

    @transaction.atomic
    def handle(self, *args, **options):
        target_name = options['target'].strip()
        sources = [s.strip() for s in options['sources']]
        dry_run = options['dry-run']

        if not target_name:
            raise CommandError('--target est requis.')
        if not any(sources):
            raise CommandError('Au moins une promotion source est requise.')

        if dry_run:
            self.stdout.write(self.style.WARNING('MODE SIMULATION (--dry-run)'))

        target, target_created = Promotion.objects.get_or_create(nom=target_name)
        total_etudiants, total_cours = 0, 0

        # Mise à jour synthétique des sources vers la cible (1 UPDATE par source).
        target_id = target.pk
        n_etudiants = (Etudiant.objects
                       .filter(promotion__nom__in=sources)
                       .update(promotion_id=target_id))
        n_cours = (Cours.objects
                   .filter(promotion__nom__in=sources)
                   .update(promotion_id=target_id))
        total_etudiants += n_etudiants
        total_cours += n_cours

        if not dry_run:
            # Les sources sont désormais vides (étudiants + cours déplacés) :
            # on supprime les promotions sources qui subsistent.
            deleted = Promotion.objects.filter(nom__in=sources).delete()[0]
        else:
            deleted = Promotion.objects.filter(nom__in=sources).count()

        if target_created:
            self.stdout.write(self.style.NOTICE(f'➕ Promotion cible créée : {target_name}'))
        self.stdout.write(
            self.style.SUCCESS(
                f'✅ {target_name} : {total_etudiants} étudiant(s) et '
                f'{total_cours} cours regroupés, {deleted} promotion(s) source(s) '
                + ('supprimées' if not dry_run else 'seraient supprimées')))

        if dry_run:
            transaction.set_rollback(True)