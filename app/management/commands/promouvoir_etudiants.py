"""Fait passer les étudiants d'une promotion à la suivante (L1 → L2 → L3).

Le passage est ce qui **constitue** l'historique : une même fiche d'étudiant
change de promotion (l'étape précédente se clôture, la nouvelle s'ouvre), au
lieu qu'un second import ne crée une fiche distincte dans la promotion
supérieure — deux fiches ne partagent aucun historique.

Quand la promotion visée contient déjà l'étudiant (même nom), les deux fiches
sont fusionnées : les inscriptions, les lignes de grille et les étapes de
parcours de la fiche en doublon sont rattachées à la fiche conservée, puis le
doublon est supprimé. L'étudiant garde ainsi, sur une seule fiche, l'année
qu'il quitte **et** celle qu'il commence.

Usage :
    python manage.py promouvoir_etudiants --de "L1 SCF LMD" --vers "L2 SCF LMD" \
        --annee 2025-2026 --dry-run
"""
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import Etudiant, HistoriquePromotion, Promotion


def _nom_normalise(noms):
    """Clé de rapprochement d'un étudiant : nom sans espaces superflus."""
    return re.sub(r'\s+', ' ', (noms or '').strip()).upper()


def _poids_notes(inscription):
    """Nombre de notes renseignées : départage deux inscriptions en doublon."""
    return sum(1 for valeur in (inscription.note_interro, inscription.note_tp,
                                inscription.note_examen)
               if valeur is not None)


def _fusionner_inscriptions(survivant, doublon):
    """Rattache au survivant les inscriptions de la fiche en doublon.

    La contrainte (étudiant, examen) interdit deux inscriptions au même
    examen : en cas de conflit, l'inscription la plus renseignée est gardée.
    """
    par_examen = {i.examen_id: i for i in survivant.inscriptions.all()}
    deplacees = 0
    for inscription in doublon.inscriptions.all():
        existante = par_examen.get(inscription.examen_id)
        if existante is None:
            inscription.etudiant = survivant
            inscription.save(update_fields=['etudiant'])
            deplacees += 1
        elif _poids_notes(inscription) > _poids_notes(existante):
            existante.delete()
            inscription.etudiant = survivant
            inscription.save(update_fields=['etudiant'])
            deplacees += 1
        else:
            inscription.delete()
    return deplacees


def _fusionner_lignes_grille(survivant, doublon):
    """Rattache au survivant les lignes de grille de la fiche en doublon."""
    deja = set(survivant.grille_lignes.values_list('grille_id', flat=True))
    deplacees = 0
    for ligne in doublon.grille_lignes.all():
        if ligne.grille_id in deja:
            ligne.delete()
        else:
            ligne.etudiant = survivant
            ligne.save(update_fields=['etudiant'])
            deja.add(ligne.grille_id)
            deplacees += 1
    return deplacees


def _fusionner_etapes(survivant, doublon):
    """Rattache au survivant les étapes de parcours de la fiche en doublon."""
    deplacees = 0
    for etape in doublon.historique_promotions.all():
        if HistoriquePromotion.objects.filter(
                etudiant=survivant, promotion=etape.promotion,
                annee_academique=etape.annee_academique).exists():
            etape.delete()
        else:
            etape.etudiant = survivant
            etape.save(update_fields=['etudiant'])
            deplacees += 1
    return deplacees

class Command(BaseCommand):
    help = ("Fait passer les étudiants d'une promotion à la suivante, en "
            "conservant leur parcours (fusion de la fiche si elle existe).")

    def add_arguments(self, parser):
        parser.add_argument('--de', dest='source', required=True,
                            help='Promotion de départ (ex. « L1 SCF LMD »).')
        parser.add_argument('--vers', dest='cible', required=True,
                            help="Promotion d'arrivée (ex. « L2 SCF LMD »).")
        parser.add_argument('--annee', default='',
                            help="Année académique de la promotion quittée "
                                 "(ex. « 2025-2026 ») : elle date l'étape "
                                 'close, donc l’année antérieure affichée.')
        parser.add_argument('--annee-cible', dest='annee_cible', default='',
                            help="Année académique de la promotion d'arrivée "
                                 "(ex. « 2026-2027 »). Sans elle, l'étape est "
                                 'datée par l’import de la grille.')
        parser.add_argument('--dry-run', dest='dry_run', action='store_true',
                            help='Simule le passage sans rien écrire en base.')

    @transaction.atomic
    def handle(self, *args, **options):
        source = Promotion.objects.filter(nom=options['source']).first()
        if source is None:
            raise CommandError(
                f'Promotion de départ introuvable : {options["source"]}')
        cible = Promotion.objects.filter(nom=options['cible']).first()
        if cible is None:
            raise CommandError(
                f'Promotion d’arrivée introuvable : {options["cible"]}')
        if source.pk == cible.pk:
            raise CommandError(
                'Les promotions de départ et d’arrivée sont identiques.')

        annee = options['annee'].strip()
        annee_cible = options['annee_cible'].strip()
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('MODE SIMULATION (--dry-run)'))

        # Fiches déjà présentes dans la promotion visée, rapprochées par nom.
        deja_dans_cible = {}
        for etudiant in Etudiant.objects.filter(promotion=cible):
            deja_dans_cible.setdefault(_nom_normalise(etudiant.noms), etudiant)

        passagers = list(Etudiant.objects.filter(promotion=source)
                         .order_by('noms'))
        fusionnes = deplaces = inscriptions = lignes = etapes = 0

        for etudiant in passagers:
            doublon = deja_dans_cible.get(_nom_normalise(etudiant.noms))
            if dry_run:
                if doublon:
                    fusionnes += 1
                else:
                    deplaces += 1
                continue

            if annee:
                # Date l'étape quittée : c'est cette année qui s'affichera
                # comme année antérieure dans la promotion d'arrivée.
                HistoriquePromotion.enregistrer(etudiant, source, annee)

            if doublon is not None:
                inscriptions += _fusionner_inscriptions(etudiant, doublon)
                lignes += _fusionner_lignes_grille(etudiant, doublon)
                etapes += _fusionner_etapes(etudiant, doublon)
                doublon.delete()
                fusionnes += 1
            else:
                deplaces += 1

            # Le changement de promotion clôture l'étape quittée et ouvre celle
            # d'arrivée — sur la même fiche, donc avec le même historique.
            etudiant.promotion = cible
            etudiant.save()
            if annee_cible:
                HistoriquePromotion.enregistrer(etudiant, cible, annee_cible)

        if dry_run:
            transaction.set_rollback(True)
            self.stdout.write(
                f'Simulation : {len(passagers)} étudiant(s) passeraient de '
                f'« {source.nom} » à « {cible.nom} » ({fusionnes} fusion(s) de '
                f'fiche, {deplaces} déplacement(s)).')
            self.stdout.write(self.style.WARNING('Aucune écriture effectuée.'))
            return

        detail = ''
        if fusionnes:
            detail = (f' — {fusionnes} fiche(s) fusionnée(s) : '
                      f'{inscriptions} inscription(s), {lignes} ligne(s) de '
                      f'grille et {etapes} étape(s) rattachées.')
        self.stdout.write(self.style.SUCCESS(
            f'{len(passagers)} étudiant(s) passés de « {source.nom} » à '
            f'« {cible.nom} » ({deplaces} déplacement(s)).' + detail))
        if annee:
            self.stdout.write(f'Étape quittée datée « {annee} ».')

