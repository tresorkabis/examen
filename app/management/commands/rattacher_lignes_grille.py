"""Rattache manuellement des lignes de grille archivées à une fiche existante.

L'import d'une grille n'apparie les étudiants que sur le **nom exact** (voir
`app.excel_import.import_grille_excel`) : c'est volontaire, les cohortes
successives partageant les mêmes numéros d'ordre. Un nom écrit différemment
dans le fichier — `KADUMBI KAYA` sur la pièce délibérée, `KADUMBI KAYA
YEDDYDIA` sur la fiche — échoue donc l'appariement, et la ligne est **archivée**
(`etudiant` NULL, nom conservé).

Ce rattachement est volontaire et doit rester explicite : cette commande le
pose à la main, sans jamais modifier le nom du fichier (traçabilité de la pièce
délibérée) ni déplacer la fiche dans une autre promotion.

Elle est aussi **rejouable** : réimporter une grille supprime et recrée ses
lignes (`Grille` est supprimée puis recréée par l'import), ce qui archive à
nouveau la ligne. Il suffit donc de relancer la même commande après chaque
réimport pour retrouver l'historique.

Rattachée à une fiche, la ligne retrouve son année dans le parcours
(`HistoriquePromotion.enregistrer`) et ses notes redeviennent visibles.

Usage :
    python manage.py rattacher_lignes_grille --grille 35 \\
        --ligne "KADUMBI KAYA=143" --ligne "LOHAMBE WELO=148" --dry-run
    python manage.py rattacher_lignes_grille --grille 35 \\
        --ligne "KADUMBI KAYA=143" --ligne "LOHAMBE WELO=148"
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import (Etudiant, Grille, GrilleEtudiant,
                        HistoriquePromotion)


class Command(BaseCommand):
    help = ("Rattache des lignes de grille archivées (nom ≠ nom de la fiche) "
            "à une fiche existante, par correspondance explicite.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--grille', type=int, required=True,
            help="Identifiant de la grille concernée.")
        parser.add_argument(
            '--ligne', action='append', required=True, dest='lignes',
            metavar='"NOM_DU_FICHIER=ID_ETUDIANT"',
            help=("Correspondance à appliquer, répétable : le nom écrit dans "
                  "le fichier, puis « = », puis l'id de la fiche. "
                  "Exemple : --ligne \"KADUMBI KAYA=143\""))
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Simule sans écrire en base.")

    def _analyser(self, argument):
        """« NOM=ID » -> (nom, id) ; refuse tout ce qui n'est pas le format."""
        nom, sep, identifiant = (argument or '').partition('=')
        nom, identifiant = nom.strip(), identifiant.strip()
        if not sep or not nom or not identifiant.isdigit():
            raise CommandError(
                f'Correspondance invalide : « {argument} ». Format attendu : '
                '"NOM_DU_FICHIER=ID_ETUDIANT".')
        return nom, int(identifiant)

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        if dry_run:
            self.stdout.write(self.style.WARNING('MODE SIMULATION (--dry-run)'))

        try:
            grille = Grille.objects.select_related('promotion').get(
                pk=options['grille'])
        except Grille.DoesNotExist:
            raise CommandError(f'Grille {options["grille"]} introuvable.')

        self.stdout.write(
            f'Grille {grille.pk} — {grille.promotion.nom} / '
            f'{grille.annee_academique} ({grille.lignes.count()} lignes)')
        self.stdout.write('')

        rattaches = deja_faits = 0
        for argument in options['lignes']:
            nom_fichier, id_etudiant = self._analyser(argument)

            try:
                etudiant = Etudiant.objects.select_related('promotion').get(
                    pk=id_etudiant)
            except Etudiant.DoesNotExist:
                raise CommandError(
                    f'Étudiant {id_etudiant} introuvable '
                    f'(ligne « {nom_fichier} »).')

            lignes = list(GrilleEtudiant.objects.filter(
                grille=grille, noms__iexact=nom_fichier))
            if not lignes:
                disponibles = ', '.join(sorted(
                    grille.lignes.values_list('noms', flat=True)))
                raise CommandError(
                    f'« {nom_fichier} » : aucune ligne de ce nom dans la '
                    f'grille {grille.pk}. Noms disponibles : {disponibles[:500]}')
            if len(lignes) > 1:
                ids = ', '.join(str(l.pk) for l in lignes)
                raise CommandError(
                    f'« {nom_fichier} » : {len(lignes)} lignes de ce nom '
                    f'(ids {ids}), rattachement ambigu : rien n\'est modifié.')

            ligne = lignes[0]
            if ligne.etudiant_id == etudiant.pk:
                deja_faits += 1
                self.stdout.write(
                    f'  = « {nom_fichier} » → {etudiant.noms} '
                    f'(déjà rattachée, ligne {ligne.pk})')
                continue
            if ligne.etudiant_id is not None:
                raise CommandError(
                    f'« {nom_fichier} » (ligne {ligne.pk}) est déjà rattachée '
                    f'à « {ligne.etudiant.noms} » : rien n\'est modifié.')

            etape = None
            cree = False
            if not dry_run:
                ligne.etudiant = etudiant
                # Le nom du fichier est conservé tel quel (traçabilité).
                ligne.save(update_fields=['etudiant'])
                # L'étape de parcours est (re)mise à jour : c'est elle qui
                # rend l'année visible dans l'historique de la fiche.
                etape, cree = HistoriquePromotion.enregistrer(
                    etudiant, grille.promotion, grille.annee_academique,
                    grille)
            rattaches += 1
            self.stdout.write(
                f'  🔗 « {nom_fichier} » (rang {ligne.rang}) → '
                f'{etudiant.noms} [{etudiant.promotion.nom}]'
                + (f' — étape {etape.annee_academique} '
                   f'{"créée" if cree else "complétée"}' if etape else ''))

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'{"Simulation" if dry_run else "Rattachement"} terminé : '
            f'{rattaches} ligne(s) rattachée(s), {deja_faits} déjà en place.'))

        if dry_run:
            transaction.set_rollback(True)
