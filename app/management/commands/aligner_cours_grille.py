"""Aligne les cours d'une promotion sur les intitulés et crédits de la grille.

L'import de la charge horaire crée les cours avec des intitulés abrégés et
parfois fautifs (« ESES », « RSX », « TR DE STAGE ET D'EMPLOI ») et un
coefficient uniforme à 1. La grille de délibération LMD (data/*.xlsx) porte,
elle, les intitulés officiels des UE et leurs crédits.

Cette commande renomme les cours de la promotion concernée avec l'intitulé de
la grille et reporte le crédit dans le champ `coefficient` : c'est le seul
champ numérique disponible, le modèle `Cours` n'ayant pas de champ « crédit »
dédié (l'interface l'affiche « Coefficient » / « Coef. »).

Les UE de la grille absentes de l'application sont créées (sans enseignant,
à compléter ensuite depuis l'interface).

Idempotente : un second passage ne modifie plus rien.

Usage :
    python manage.py aligner_cours_grille --dry-run   # simulation
    python manage.py aligner_cours_grille             # écriture en base
"""
from pathlib import Path

import openpyxl
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from app.models import Cours, Promotion

DATA_DIR = Path(__file__).resolve().parents[3] / 'data'
ENTETE_UE = "UNITES D'ENSEIGNEMENT"

# Grilles prises en charge : fichier -> promotion.
GRILLES = {
    'L1 INFO LMD A_2025_2026.xlsx': 'L1 INFO A',
    'L2 SCF_LMD 2025_2026.xlsx': 'L2 SCF LMD',
}

# UE de la grille (intitulé exact) -> nom du cours créé par la charge horaire.
# `None` = l'UE n'existe pas encore en base et doit être créée.
CORRESPONDANCES_L1 = {
    'Informatique Générale': 'INFO GENERALE',
    'Bureautique': 'LABORATOIRE',
    'Analyse Informatique': 'ANALYSE INFORMATIQUE',
    'Algorithmique 1': 'ALGORITHMIQUE',
    'Anglais des TIC': 'ANGLAIS DES TIC',
    'Français 1': 'Français',
    'Introduction à la recherche': 'IRS',
    'Culture et citoyenneté': 'CULTURE - CITOYENNETE',
    'Environnement': 'CULTURE - ENVIRONNEMENT',
    'Element de Droit': 'DROIT CIVIL',
    'Mathématique pour ingénieur': 'MATHEMATIQUES APPLIQUES',
    'Statistique descriptive': 'STATISTIQUE',
    'Mathématique Financière': 'MATH FINANCIERES',
    'Bilant professionnel': 'BILAN ET PROJET PRO',
    'Langage Python': 'PYTHON',
    'Langage C': 'LANGAGE C',
    'Programmation Web - 1': 'DEVELOPPEMENT WEB',
    'Technique de prod. de logiciels': 'TECH DE PROD DE LOGICIEL',
    "Système d'exploitation": "SYSTÈME D'EXPLOITATION",
    'Base des données': 'BASE DE DONNEES',
    'Architecture des ordinateurs': 'ARCHITECTURE DES ORDI',
    'Initiation aux réseux info': 'INTRO AUX RSX INFO',
    'Organisation des entreprises': 'ORGANISATION DES ESES',
    "Introduction à l'économie": "INTRO A L'ECONOMIE",
    'Comptabilité générale': 'COMPTABILITE GENERALE',
    'Electricité générale': 'ELECTRICITE GENERALE',
    'Tech recherch emploi': "TR DE STAGE ET D'EMPLOI",
    'Stage d\'observation': None,
    'Projet Tutoré': 'PROJET TUTORE',
}

# Fautes de frappe présentes dans la grille L1 : intitulé orthographié
# correctement retenu pour le cours.
CORRECTIONS_L1 = {
    'Bilant professionnel': 'Bilan professionnel',
    'Initiation aux réseux info': 'Initiation aux réseaux info',
}

# UE de la grille L2 SCF (intitulé exact) -> cours en base. `None` = à créer.
# Les intitulés courts ou fautifs de la charge horaire sont résolus ici.
CORRESPONDANCES_L2_SCF = {
    'Comptabilité des sociétés': 'COMPTABILITE DES SOCIETES',
    'Analyse financière': 'ANALYSE FINANCIERE',
    'Eléments de droit commercial': 'DROIT COMMERCIAL',
    'Elément de droit fiscal': 'DROIT FISCAL',
    'Statistique Inférentielle': 'STATISTIQUE INFERENTIELLE',
    'Méthode de Recherche Scientifique': 'MRS',
    "Technique d'enquête": "METHODE ET TECH D'ENQUETE",
    'Gestion financière': 'GESTION FINANCIERE',
    'Gestion de la production': 'GESTION DE LA PRODUCTION',
    'Gestion des ressources humaines': 'GRH',
    'Eléments de recherche opérationnelle': 'RO',
    'Comptabilité de gestion': 'COMPTABILITE DE GESTION',
    'Droit du travail': 'DROIT DE TRAVAIL',
    'Droit administratif': 'DROIT ADMINISTRATIF',
    "Eléments d'entrepreneuriat": 'ENTREPREUNARIAT',
    'Gestion des projets': 'GESTION DES PROJETS',
    'Gestion des bases de données': 'SGBD',
    'Bureautique': 'BUREAUTIQUE 2',
    'Anglais des affaires 1': 'ANGLAIS DES AFFAIRES',
    'Pratique professionnelle 2': 'PRATIQUE PROFESSIONNELLE',
    "Stage d'intervention": None,
    'Projet tutoré 1': None,
}

# Fichier -> (promotion, correspondances, corrections).
CONFIGS = {
    'L1 INFO LMD A_2025_2026.xlsx': (
        'L1 INFO A', CORRESPONDANCES_L1, CORRECTIONS_L1),
    'L2 SCF_LMD 2025_2026.xlsx': (
        'L2 SCF LMD', CORRESPONDANCES_L2_SCF, {}),
}



def _meme_en_tete(valeur):
    """La cellule est-elle l'en-tête « UNITES D'ENSEIGNEMENT » ?"""
    if not isinstance(valeur, str):
        return False
    return (valeur.strip().upper().replace(' ', '')
            == ENTETE_UE.replace(' ', ''))


def lire_grille(chemin):
    """Retourne la liste [(intitulé, crédits)] des UE de la grille.

    La ligne d'en-tête « UNITES D'ENSEIGNEMENT » (colonne B) est repérée, la
    ligne « Crédits » est celle qui suit. Les colonnes sans intitulé d'UE
    (totaux `Total crédit validé`, en-tête de semestre) sont ignorées : c'est
    ce qui sépare naturellement les deux semestres.
    """
    classeur = openpyxl.load_workbook(chemin, data_only=True)
    feuille = classeur.worksheets[0]

    ligne_ue = None
    for ligne in range(1, feuille.max_row + 1):
        if _meme_en_tete(feuille.cell(ligne, 2).value):
            ligne_ue = ligne
            break
    if ligne_ue is None:
        raise CommandError(
            f"En-tête « {ENTETE_UE} » introuvable dans {chemin.name} : "
            "ce fichier n'a pas le format d'une grille de délibération.")

    ligne_credits = ligne_ue + 1
    ues = []
    for colonne in range(3, feuille.max_column + 1):
        nom = feuille.cell(ligne_ue, colonne).value
        if not isinstance(nom, str) or not nom.strip():
            # Colonnes de synthèse (`Total crédit validé`) et zone annexe.
            continue
        if 'total' in nom.strip().lower():
            # Colonne de synthèse (`Total crédit validé`, `Total Général
            # crédit`) : elle porte un intitulé et un crédit numérique mais
            # n'est pas une UE.
            continue
        credit = feuille.cell(ligne_credits, colonne).value
        if isinstance(credit, bool) or not isinstance(credit, (int, float)):
            # Marqueur de fin de tableau (« FIN », cellule BT7 du fichier) :
            # une UE sans crédit n'en est pas une.
            continue
        ues.append((' '.join(nom.split()), int(credit)))
    if not ues:
        raise CommandError(f"Aucune UE lue dans la grille {chemin.name}.")
    return ues


class Command(BaseCommand):
    help = ("Renomme les cours d'une promotion selon la grille de "
            "délibération et reporte les crédits dans le coefficient.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help="Affiche les modifications sans écrire en base")
        parser.add_argument(
            '--grille', default=None,
            help="Nom du fichier de grille à traiter (défaut : toutes)")

    @transaction.atomic
    def handle(self, *args, **options):
        dry_run = options['dry_run']
        selection = options.get('grille')

        if dry_run:
            self.stdout.write(self.style.WARNING('MODE SIMULATION (--dry-run)'))
        if selection and selection not in CONFIGS:
            raise CommandError(
                f"Grille « {selection} » inconnue. Choix : "
                f"{', '.join(sorted(CONFIGS))}.")

        fichiers = ([selection] if selection else list(CONFIGS))
        traitees = 0
        for nom in fichiers:
            traitees += self._aligner(nom, dry_run)

        if not selection and traitees == 0:
            raise CommandError(
                "Aucun fichier de grille trouvé dans "
                f"{DATA_DIR} (attendus : {', '.join(sorted(CONFIGS))}).")

        if dry_run:
            transaction.set_rollback(True)

    def _aligner(self, nom_fichier, dry_run):
        """Aligne une promotion sur sa grille. Retourne 1 si traitée, 0 sinon."""
        nom_promotion, correspondances, corrections = CONFIGS[nom_fichier]
        chemin = DATA_DIR / nom_fichier

        if not chemin.exists():
            # Balayage multi-grilles (tests, poste sans data/...) : on signale
            # et on passe à la suivante. Un appel explicite (--grille) reste
            # strict pour ne pas masquer une faute de frappe.
            message = f'Fichier introuvable : {chemin}'
            if getattr(self, '_grille_explicite', False):
                raise CommandError(message)
            self.stdout.write(self.style.WARNING(f'  ⏭️  {message} (ignorée)'))
            return 0

        promotion = Promotion.objects.filter(nom=nom_promotion).first()
        if promotion is None:
            raise CommandError(
                f"Promotion « {nom_promotion} » introuvable en base.")

        ues = lire_grille(chemin)
        self.stdout.write(
            f'{chemin.name} → {promotion.nom} : {len(ues)} UE lues\n')

        cours_par_nom = {c.nom: c for c in promotion.cours.all()}
        renommes = maj_credits = crees = alignes = 0
        traites = set()

        for intitule_grille, credit in ues:
            intitule = corrections.get(intitule_grille, intitule_grille)
            cours = cours_par_nom.get(intitule)
            ancien_nom = correspondances.get(intitule_grille)
            if cours is None and ancien_nom:
                cours = cours_par_nom.get(ancien_nom)

            if cours is None:
                self.stdout.write(
                    f'  ➕ {intitule} (crédit {credit}) : absent de '
                    f"l'application, à créer")
                crees += 1
                if not dry_run:
                    cours = Cours.objects.create(
                        nom=intitule, coefficient=credit, promotion=promotion)
                    cours_par_nom[intitule] = cours
                    traites.add(cours.pk)
                continue

            if cours.pk in traites:
                raise CommandError(
                    f'« {intitule} » et une autre UE de la grille désignent '
                    f'le même cours (id={cours.pk}, « {cours.nom} »).')
            traites.add(cours.pk)

            changements = []
            if cours.nom != intitule:
                changements.append(f'« {cours.nom} » → « {intitule} »')
                renommes += 1
            if cours.coefficient != credit:
                changements.append(f'crédit {cours.coefficient} → {credit}')
                maj_credits += 1

            if not changements:
                alignes += 1
                continue

            self.stdout.write(f'  ✏️  {intitule} : ' + ', '.join(changements))
            if not dry_run:
                if ancien_nom and ancien_nom != intitule:
                    cours_par_nom.pop(ancien_nom, None)
                cours.nom = intitule
                cours.coefficient = credit
                cours.save(update_fields=['nom', 'coefficient'])
                cours_par_nom[intitule] = cours

        for cours in promotion.cours.all():
            if cours.pk not in traites:
                self.stdout.write(self.style.WARNING(
                    f'  ⚠️  {cours.nom} : cours en base non couvert '
                    f'par la grille'))

        self.stdout.write(self.style.SUCCESS(
            f'{"Simulation" if dry_run else "Alignement"} terminé : '
            f'{renommes} renommage(s), {maj_credits} crédit(s) mis à jour, '
            f'{crees} cours créé(s), {alignes} déjà aligné(s).'))

        return 1

