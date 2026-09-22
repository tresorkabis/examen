import re
import unicodedata
from decimal import Decimal, InvalidOperation

import openpyxl
import pandas as pd
from django.db import transaction
from app.models import (Etudiant, Enseignant, Cours, Promotion,
                        Grille, GrilleUE, GrilleEtudiant, GrilleNote,
                        HistoriquePromotion)

PROMOTION_ALIASES = {
    'L3 INFO': 'L3 INFO A',
    'L3 INFO LMD': 'L3 INFO A',
    'L3 INFO LMD A': 'L3 INFO A',
    'L2 TS LMD A': 'L2 SDA',
    'L2 TS A': 'L2 SDA',
    'L2 SD A': 'L2 SDA',
    'L3 TS LMD A': 'L3 SDA',
    'L3 TS A': 'L3 SDA',
    'L3 SD A': 'L3 SDA',
}

# Codes de décision d'échec selon les grilles : « NV » (non validé),
# « A » (Ajourné, ex. L3 INFO), « AJ ». Même verdict, graphies différentes.
DECISIONS_ECHEC = {'NV', 'A', 'AJ', 'AJOURNE'}


def normalize_promotion_name(name):
    """Mappe les variations et alias des noms de promotions vers un nom officiel unique."""
    if not name:
        return name
    clean_name = str(name).strip()
    return PROMOTION_ALIASES.get(clean_name, clean_name)


def slugify(text):
    """Convertit un texte en minuscules, sans accents ni caractères spéciaux."""
    if not text:
        return ''
    text = unicodedata.normalize('NFKD', str(text))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]', '', text.lower())


def generate_unique_student_email(nom, prenom, pris=None):
    """Génère un email unique pour un étudiant.

    `pris` : ensemble des emails déjà utilisés. Le passer évite une requête
    SQL par tentative (sinon on retombe sur un `exists()` à chaque essai).
    """
    base = f"{slugify(prenom)}.{slugify(nom)}" or 'etudiant'
    email = f"{base}@example.com"
    n = 2
    while _email_pris(email, pris):
        email = f"{base}{n}@example.com"
        n += 1
    return email


def generate_unique_teacher_email(full_name, pris=None):
    """Génère un email unique pour un enseignant à partir de son nom complet."""
    parts = [slugify(part) for part in str(full_name).split()]
    base = '.'.join(part for part in parts if part) or 'enseignant'
    email = f"{base}@example.com"
    n = 2
    while _email_pris(email, pris):
        email = f"{base}{n}@example.com"
        n += 1
    return email


def _email_pris(email, pris=None):
    """L'email est-il déjà utilisé ? (aucune requête si `pris` est fourni)."""
    if pris is not None:
        return email in pris
    return Enseignant.objects.filter(email=email).exists()


def _numero_ordre(valeur):
    """Numéro d'ordre d'une cellule de grille (int), ou None si non numérique.

    Indispensable : pandas type la colonne des numéros en `float` dès qu'une
    cellule est vide (totaux, signatures…), si bien que `str(1.0)` vaut
    `'1.0'` — ce qu'un simple `re.match(r'^\\d{1,3}$')` rejette. Toute une
    grille pouvait ainsi être ignorée silencieusement.
    """
    if valeur is None or pd.isna(valeur):
        return None
    try:
        return int(float(str(valeur).strip()))
    except (TypeError, ValueError):
        return None


class ReferentielImport:
    """Cache en mémoire partagé par un import Excel.

    Sans ce cache, chaque ligne déclenchait plusieurs `get_or_create()` /
    `filter().first()` : de 3 à 6 requêtes SQL par ligne. Ici les référentiels
    (promotions, enseignants, cours) sont chargés une seule fois puis
    interrogés depuis des dictionnaires ; seules les écritures réellement
    nécessaires atteignent la base.

    Les étudiants sont, eux, chargés promotion par promotion (une promotion
    pouvant contenir plusieurs milliers d'étudiants).
    """

    def __init__(self):
        self.promotions = {p.nom: p for p in Promotion.objects.all()}
        # `order_by('id')` + `setdefault` : on garde le plus petit id, comme le
        # faisait `filter(noms__iexact=...).order_by('id').first()`.
        self.enseignants = {}
        self.emails = {}                      # email -> pk du propriétaire
        for enseignant in Enseignant.objects.order_by('id'):
            self.enseignants.setdefault(enseignant.noms.casefold(), enseignant)
            self.emails.setdefault(enseignant.email, enseignant.pk)
        self.cours = {}
        for cours in Cours.objects.order_by('id'):
            self.cours.setdefault((cours.promotion_id, cours.nom), cours)
        self.etudiants = {}                   # (promotion_id, noms) -> Etudiant
        self.etudiants_par_numero = {}        # numero -> Etudiant
        self._promotions_chargees = set()
        # Matricules existants (une seule requête) : permet de retrouver un
        # étudiant dont la promotion n'a pas encore été chargée (fichier
        # réutilisant un matricule d'une autre promotion) sans requête par
        # ligne dans le cas courant.
        self.numeros = {}
        for numero, pk in Etudiant.objects.values_list('numero_etudiant', 'id'):
            self.numeros.setdefault(numero, pk)

    # --- Promotions ------------------------------------------------------
    def promotion(self, nom):
        """Promotion `nom`, créée si nécessaire (1 requête la première fois)."""
        if not nom:
            return None
        promotion = self.promotions.get(nom)
        if promotion is None:
            promotion, _ = Promotion.objects.get_or_create(nom=nom)
            self.promotions[nom] = promotion
        return promotion

    # --- Enseignants -----------------------------------------------------
    def enseignant(self, noms, email=None):
        """Enseignant nommé `noms` (casse/Unicode ignorés).

        Retourne `(enseignant, cree)`. Si `email` est omis, un email unique
        est généré — sans requête, grâce au jeu d'emails déjà chargé.
        """
        cle = noms.casefold()
        enseignant = self.enseignants.get(cle)
        if enseignant is not None:
            return enseignant, False
        if not email:
            email = generate_unique_teacher_email(noms, pris=self.emails)
        enseignant = Enseignant.objects.create(noms=noms, email=email)
        self.enseignants[cle] = enseignant
        self.emails.setdefault(email, enseignant.pk)
        return enseignant, True

    def email_libre(self, email, enseignant):
        """L'email est-il libre (ou déjà porté par `enseignant`) ?"""
        return self.emails.get(email, enseignant.pk) == enseignant.pk

    def reserver_email(self, email, enseignant):
        """Marque `email` comme utilisé (après modification de `enseignant`)."""
        self.emails[email] = enseignant.pk

    # --- Cours -----------------------------------------------------------
    def cours_existant(self, nom, promotion):
        """Cours `nom` de `promotion`, ou None (aucune requête)."""
        return self.cours.get((promotion.pk, nom))

    def ajouter_cours(self, cours):
        self.cours[(cours.promotion_id, cours.nom)] = cours

    # --- Étudiants -------------------------------------------------------
    def _charger_etudiants(self, promotion):
        """Charge les étudiants de `promotion` (une requête, une seule fois)."""
        if promotion.pk in self._promotions_chargees:
            return
        self._promotions_chargees.add(promotion.pk)
        for etudiant in Etudiant.objects.filter(promotion=promotion):
            self.etudiants[(promotion.pk, etudiant.noms)] = etudiant
            self.etudiants_par_numero.setdefault(
                etudiant.numero_etudiant, etudiant)

    def etudiant(self, promotion, noms=None, numero=None):
        """Étudiant de `promotion` identifié par `numero` ou par ses noms."""
        self._charger_etudiants(promotion)
        if numero:
            return self.etudiants_par_numero.get(numero)
        return self.etudiants.get((promotion.pk, noms))

    def ajouter_etudiant(self, etudiant):
        """Indexe un étudiant qui vient d'être créé ou mis à jour.

        `setdefault` sur la clé « noms » : en cas d'homonymes dans une même
        promotion, on conserve le plus petit id — exactement ce que faisait
        l'ancien `filter(noms__iexact=...).order_by('id').first()`.
        """
        self.etudiants.setdefault((etudiant.promotion_id, etudiant.noms),
                                  etudiant)
        self.etudiants_par_numero[etudiant.numero_etudiant] = etudiant

    def maj_etudiant(self, etudiant, noms, promotion, email=None):
        """Met à jour l'étudiant *seulement* si une valeur a changé.

        Un ré-import du même fichier ne génère ainsi aucun UPDATE (retourne
        False, la ligne est comptée comme ignorée). Retourne True si
        l'enregistrement a eu lieu.
        """
        ancienne_cle = (etudiant.promotion_id, etudiant.noms)
        modifie = False
        if etudiant.noms != noms:
            etudiant.noms = noms
            modifie = True
        if etudiant.promotion_id != promotion.pk:
            etudiant.promotion = promotion
            modifie = True
        if email and etudiant.email != email:
            etudiant.email = email
            modifie = True
        if not modifie:
            return False
        if ancienne_cle != (promotion.pk, noms):
            self.etudiants.pop(ancienne_cle, None)   # clé devenue obsolète
        etudiant.save()
        self.ajouter_etudiant(etudiant)
        return True


def _normalize_header(col_name):
    """Normalise le nom d'une colonne (minuscules, sans accents ni espaces)."""
    if not col_name:
        return ''
    text = str(col_name).lower().replace('n°', 'num').replace('#', 'num')
    return slugify(text)


def import_etudiants_excel(file_obj, default_promotion_id=None):
    """
    Importe les étudiants depuis un fichier Excel.
    Gère les tableaux avec en-têtes et les grilles de délibération.
    """
    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    try:
        excel_file = pd.ExcelFile(file_obj)
    except Exception as e:
        return {
            'success': False,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': [f"Impossible de lire le fichier Excel : {e}"]
        }

    default_promotion = None
    if default_promotion_id:
        default_promotion = Promotion.objects.filter(pk=default_promotion_id).first()

    # Cache : promotions, étudiants et numéros sont lus une seule fois.
    referentiel = ReferentielImport()

    with transaction.atomic():
        for sheet_name in excel_file.sheet_names:
            try:
                df = excel_file.parse(sheet_name)
            except Exception as e:
                errors.append(f"Feuille '{sheet_name}' ignorée : {e}")
                continue

            if df.empty:
                continue

            # Tenter de détecter les en-têtes
            cols_normalized = [_normalize_header(c) for c in df.columns]

            has_nom_header = any('nom' in c for c in cols_normalized)
            has_prenom_header = any('prenom' in c for c in cols_normalized)
            has_full_name_header = any(c in ('etudiant', 'noms', 'nomcomplet', 'nometprenom') for c in cols_normalized)

            if has_nom_header or has_prenom_header or has_full_name_header:
                # Import basé sur les en-têtes nommés
                col_map = {orig: norm for orig, norm in zip(df.columns, cols_normalized)}
                
                # Chercher la meilleure colonne pour chaque champ
                nom_col = next((c for c, n in col_map.items() if n in ('nom', 'noms')), None)
                prenom_col = next((c for c, n in col_map.items() if 'prenom' in n), None)
                full_name_col = next((c for c, n in col_map.items() if n in ('etudiant', 'nomcomplet', 'nometprenom', 'nomscomplet')), None)
                numero_col = next((c for c, n in col_map.items() if any(k in n for k in ('numero', 'matricule', 'num', 'code'))), None)
                email_col = next((c for c, n in col_map.items() if 'email' in n or 'mail' in n), None)
                promo_col = next((c for c, n in col_map.items() if 'promotion' in n or 'promo' in n or 'filiere' in n), None)

                for idx, row in df.iterrows():
                    line_num = idx + 2  # Excel 1-based header row + 1
                    try:
                        # Extraire Noms
                        if nom_col and not pd.isna(row[nom_col]):
                            nom_str = str(row[nom_col]).strip()
                            prenom_str = str(row[prenom_col]).strip() if prenom_col and not pd.isna(row[prenom_col]) else ''
                            noms_val = f"{nom_str} {prenom_str}".strip()
                        elif full_name_col and not pd.isna(row[full_name_col]):
                            noms_val = str(row[full_name_col]).strip()
                        else:
                            skipped_count += 1
                            continue

                        if not noms_val:
                            skipped_count += 1
                            continue

                        # Promotion (cache : une requête la première fois)
                        promo_obj = default_promotion
                        if promo_col and not pd.isna(row[promo_col]):
                            promo_name = normalize_promotion_name(str(row[promo_col]).strip())
                            if promo_name:
                                promo_obj = referentiel.promotion(promo_name) or promo_obj

                        if not promo_obj:
                            # Utiliser le nom de la feuille comme promotion si valide
                            if sheet_name and not sheet_name.lower().startswith('sheet'):
                                promo_obj = referentiel.promotion(
                                    normalize_promotion_name(sheet_name.strip()))
                            else:
                                promo_obj = referentiel.promotion('Promotion Générale')

                        # Numéro étudiant (optionnel, généré par le modèle si absent)
                        numero = str(row[numero_col]).strip() if numero_col and not pd.isna(row[numero_col]) else None

                        # Email (optionnel)
                        email = str(row[email_col]).strip() if email_col and not pd.isna(row[email_col]) else None

                        # Enregistrement (aucune requête de lecture : le
                        # cache de la promotion fournit l'étudiant).
                        etu = referentiel.etudiant(
                            promo_obj, noms=noms_val, numero=numero)
                        if etu is None:
                            etu = Etudiant(noms=noms_val, email=email,
                                           promotion=promo_obj)
                            if numero:
                                etu.numero_etudiant = numero
                            etu.save()
                            referentiel.ajouter_etudiant(etu)
                            created_count += 1
                        elif referentiel.maj_etudiant(
                                etu, noms_val, promo_obj, email):
                            updated_count += 1
                        else:
                            skipped_count += 1

                    except Exception as row_err:
                        errors.append(f"Feuille '{sheet_name}', ligne {line_num} : {row_err}")

            else:
                # Mode grille de délibération LMD (col 0 = num d'ordre, col 1 = nom complet)
                df_raw = excel_file.parse(sheet_name, header=None)
                promo_obj = default_promotion
                if not promo_obj:
                    promo_obj = referentiel.promotion(
                        normalize_promotion_name(sheet_name.strip()))

                promo_slug = slugify(promo_obj.nom).upper() or 'ETU'
                seen_names = set()

                for idx in range(df_raw.shape[0]):
                    try:
                        rang = _numero_ordre(df_raw.iat[idx, 0])
                        val_name = (str(df_raw.iat[idx, 1]).strip()
                                    if df_raw.shape[1] > 1
                                    and not pd.isna(df_raw.iat[idx, 1]) else '')

                        if rang is None or not 0 < rang < 1000 or not val_name:
                            continue   # en-têtes, totaux, signatures

                        val_name = re.sub(r'\s+', ' ', val_name)
                        if val_name.upper() in seen_names:
                            skipped_count += 1
                            continue
                        seen_names.add(val_name.upper())

                        noms_val = val_name
                        numero = f"{promo_slug}-{rang:03d}"

                        etu = referentiel.etudiant(promo_obj, numero=numero)
                        if etu is None:
                            etu = Etudiant(noms=noms_val, promotion=promo_obj,
                                           numero_etudiant=numero)
                            etu.save()
                            referentiel.ajouter_etudiant(etu)
                            created_count += 1
                        elif referentiel.maj_etudiant(etu, noms_val, promo_obj):
                            updated_count += 1
                        else:
                            skipped_count += 1

                    except Exception as row_err:
                        errors.append(f"Feuille '{sheet_name}', ligne {idx + 1} : {row_err}")


    return {
        'success': True,
        'created': created_count,
        'updated': updated_count,
        'skipped': skipped_count,
        'errors': errors
    }


def import_enseignants_excel(file_obj):
    """
    Importe les enseignants depuis un fichier Excel.
    """
    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    try:
        excel_file = pd.ExcelFile(file_obj)
    except Exception as e:
        return {
            'success': False,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': [f"Impossible de lire le fichier Excel : {e}"]
        }

    referentiel = ReferentielImport()

    with transaction.atomic():
        for sheet_name in excel_file.sheet_names:
            try:
                df = excel_file.parse(sheet_name)
            except Exception as e:
                errors.append(f"Feuille '{sheet_name}' ignorée : {e}")
                continue

            if df.empty:
                continue

            cols_normalized = [_normalize_header(c) for c in df.columns]
            col_map = {orig: norm for orig, norm in zip(df.columns, cols_normalized)}

            nom_col = next((c for c, n in col_map.items() if n in ('nom', 'noms')), None)
            prenom_col = next((c for c, n in col_map.items() if 'prenom' in n), None)
            full_name_col = next((c for c, n in col_map.items() if any(k in n for k in ('enseignant', 'prof', 'professeur', 'titulaire', 'nomcomplet'))), None)
            email_col = next((c for c, n in col_map.items() if 'email' in n or 'mail' in n), None)

            for idx, row in df.iterrows():
                line_num = idx + 2
                try:
                    if nom_col and not pd.isna(row[nom_col]):
                        nom = str(row[nom_col]).strip()
                        prenom = str(row[prenom_col]).strip() if prenom_col and not pd.isna(row[prenom_col]) else ''
                        noms_val = f"{nom} {prenom}".strip()
                        noms_val = ' '.join(noms_val.split())
                    elif full_name_col and not pd.isna(row[full_name_col]):
                        noms_val = str(row[full_name_col]).strip()
                        noms_val = ' '.join(noms_val.split())
                        if 'total' in noms_val.lower():
                            skipped_count += 1
                            continue
                    else:
                        skipped_count += 1
                        continue

                    if not noms_val:
                        skipped_count += 1
                        continue

                    if email_col and not pd.isna(row[email_col]):
                        email = str(row[email_col]).strip()
                    else:
                        email = None

                    # Anti-doublons : même noms (insensible casse/accents) =
                    # un seul enseignant. L'email reste unique mais n'est plus
                    # la clé de déduplication (chaque import générait
                    # babanemi.albert, babanemi.albert2, ...). Email généré
                    # paresseusement, depuis le cache, uniquement à la création.
                    ens, cree = referentiel.enseignant(noms_val, email)
                    if cree:
                        created_count += 1
                    else:
                        updated = False
                        if email and ens.email != email and \
                                referentiel.email_libre(email, ens):
                            ens.email = email
                            updated = True
                        if ens.noms != noms_val:
                            ens.noms = noms_val
                            updated = True
                        if updated:
                            ens.save()
                            referentiel.reserver_email(ens.email, ens)
                            updated_count += 1
                        else:
                            skipped_count += 1

                except Exception as row_err:
                    errors.append(f"Feuille '{sheet_name}', ligne {line_num} : {row_err}")

    return {
        'success': True,
        'created': created_count,
        'updated': updated_count,
        'skipped': skipped_count,
        'errors': errors
    }


def import_cours_excel(file_obj, default_promotion_id=None):
    """
    Importe les cours depuis un fichier Excel.
    Gère les feuilles de charge horaire et les listes simples.
    """
    created_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []

    try:
        excel_file = pd.ExcelFile(file_obj)
    except Exception as e:
        return {
            'success': False,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': [f"Impossible de lire le fichier Excel : {e}"]
        }

    default_promotion = None
    if default_promotion_id:
        default_promotion = Promotion.objects.filter(pk=default_promotion_id).first()

    referentiel = ReferentielImport()

    with transaction.atomic():
        for sheet_name in excel_file.sheet_names:
            try:
                df = excel_file.parse(sheet_name)
            except Exception as e:
                errors.append(f"Feuille '{sheet_name}' ignorée : {e}")
                continue

            if df.empty:
                continue

            cols_normalized = [_normalize_header(c) for c in df.columns]
            col_map = {orig: norm for orig, norm in zip(df.columns, cols_normalized)}

            cours_col = next((c for c, n in col_map.items() if any(k in n for k in ('cours', 'intitule', 'matiere', 'nom'))), None)
            promo_col = next((c for c, n in col_map.items() if 'promotion' in n or 'promo' in n or 'filiere' in n), None)
            ens_col = next((c for c, n in col_map.items() if any(k in n for k in ('enseignant', 'prof', 'professeur', 'titulaire'))), None)
            coeff_col = next((c for c, n in col_map.items() if 'coeff' in n or 'credit' in n), None)

            if not cours_col:
                errors.append(f"Feuille '{sheet_name}' : aucune colonne de cours identifiée.")
                continue

            for idx, row in df.iterrows():
                line_num = idx + 2
                try:
                    cours_nom = str(row[cours_col]).strip() if not pd.isna(row[cours_col]) else ''
                    if not cours_nom or cours_nom.lower() in ('total', 'nan'):
                        skipped_count += 1
                        continue

                    # Promotion (cache : une requête la première fois)
                    promo_obj = default_promotion
                    if promo_col and not pd.isna(row[promo_col]):
                        promo_name = normalize_promotion_name(str(row[promo_col]).strip())
                        if promo_name:
                            promo_obj = referentiel.promotion(promo_name) or promo_obj

                    if not promo_obj:
                        if sheet_name and not sheet_name.lower().startswith('sheet'):
                            promo_obj = referentiel.promotion(
                                normalize_promotion_name(sheet_name.strip()))
                        else:
                            promo_obj = referentiel.promotion('Promotion Générale')


                    # Enseignant (cache : plus de SELECT ni d'email calculé
                    # pour les enseignants déjà connus)
                    enseignant_obj = None
                    if ens_col and not pd.isna(row[ens_col]):
                        noms_val = ' '.join(str(row[ens_col]).split())
                        if noms_val and 'total' not in noms_val.lower():
                            enseignant_obj, _ = referentiel.enseignant(noms_val)

                    # Coefficient
                    coefficient = 1
                    if coeff_col and not pd.isna(row[coeff_col]):
                        try:
                            coefficient = int(row[coeff_col])
                        except (ValueError, TypeError):
                            coefficient = 1

                    cours = referentiel.cours_existant(cours_nom, promo_obj)
                    if cours is None:
                        cours = Cours.objects.create(
                            nom=cours_nom,
                            promotion=promo_obj,
                            enseignant=enseignant_obj,
                            coefficient=coefficient
                        )
                        referentiel.ajouter_cours(cours)
                        created_count += 1
                    else:
                        updated = False
                        if cours.enseignant_id != (enseignant_obj.id if enseignant_obj else None):
                            cours.enseignant = enseignant_obj
                            updated = True
                        if cours.coefficient != coefficient:
                            cours.coefficient = coefficient
                            updated = True
                        if updated:
                            cours.save()
                            updated_count += 1
                        else:
                            skipped_count += 1

                except Exception as row_err:
                    errors.append(f"Feuille '{sheet_name}', ligne {line_num} : {row_err}")

    return {
        'success': True,
        'created': created_count,
        'updated': updated_count,
        'skipped': skipped_count,
        'errors': errors
    }
# --- Grilles de délibération ------------------------------------------------

# Repères du fichier de grille (cf. data/L1 INFO LMD A_2025_2026.xlsx).
_ENTETE_UE = "UNITES D'ENSEIGNEMENT"
_LIBELLE_CREDITS = 'Crédits'


def _texte(valeur):
    """Chaîne « propre » d'une cellule (espaces multiples réduits), ou ''."""
    if valeur is None or isinstance(valeur, bool):
        return ''
    return ' '.join(str(valeur).split())


def _entier(valeur, defaut=0):
    """Entier d'une cellule, ou `defaut` si la cellule n'est pas numérique."""
    if valeur is None or isinstance(valeur, bool):
        return defaut
    try:
        return int(float(str(valeur).strip()))
    except (TypeError, ValueError):
        return defaut


def _decimal(valeur, defaut=None):
    """Décimal d'une cellule, ou `defaut` si non numérique.

    Comme `_entier`, mais sans troncature : le total pondéré peut être
    décimal quand une note l'est (731,2), et le tronquer créerait un écart
    artificiel au contrôle d'intégrité.
    """
    if valeur is None or isinstance(valeur, bool):
        return defaut
    try:
        return Decimal(str(valeur).replace(',', '.')).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return defaut


def _note(valeur):
    """Note /20 d'une cellule, ou None pour une case vide / illisible.

    Les grilles utilisent plusieurs écritures pour l'absence de note :
    cellule vide, « 0 », ou tirets de présentation (barème, signatures).
    """
    if valeur is None or isinstance(valeur, bool):
        return None
    try:
        return Decimal(str(valeur).replace(',', '.')).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        return None


def _est_en_tete_ue(valeur):
    return _texte(valeur).upper().replace(' ', '') == _ENTETE_UE.replace(' ', '')


def _alerte_credits(lignes, ues):
    """Alerte si le total pondéré saisi diverge des notes (contrôle d'intégrité).

    Le fichier calcule « crédits × note » ; `total_pondere` y est pourtant
    saisi en dur. Un écart trahit une note modifiée après coup, ou un total
    jamais recalculé : c'est exactement ce qu'un import doit signaler plutôt
    que de le corriger en silence.
    """
    credits_par_colonne = {ue['colonne']: ue['credits'] for ue in ues}
    ecarts = []
    for ligne in lignes:
        if not ligne['total_pondere']:
            continue
        calcule = sum(
            note * credits_par_colonne[colonne]
            for colonne, note in ligne['notes'].items() if note is not None)
        if calcule != ligne['total_pondere']:
            ecarts.append(
                f"{ligne['noms']} : total pondéré saisi "
                f"{ligne['total_pondere']}, recalculé {calcule}")
    if not ecarts:
        return None
    if len(ecarts) == 1:
        return f"Écart de total pondéré — {ecarts[0]}"
    return (f"Écarts de total pondéré ({len(ecarts)}) : "
            + ' ; '.join(ecarts[:5]) + ('…' if len(ecarts) > 5 else ''))


def _alerte_decisions(lignes, ues, total_credits):
    """Alerte si une décision contredit le barème officiel de la grille.

    Barème du fichier : crédits validés (note ≥ 10) par semestre, décision
    `V` si toutes les UE sont acquises, sinon `VC` au-delà de 9,5/20 pondéré,
    `NV` en dessous ; mention calculée sur la moyenne.

    Les grilles notent l'échec sous plusieurs codes — `NV` (L1 INFO),
    `A` (Ajourné, L3 INFO), `AJ`… — qui désignent le même verdict : aucune
    alerte entre eux. En revanche `V` ou `VC` face à un échec attendu reste
    un vrai écart.
    """
    ecarts = []
    for ligne in lignes:
        ue_par_colonne = {ue['colonne']: ue for ue in ues}
        acquises = [ue_par_colonne[colonne]
                    for colonne, note in ligne['notes'].items()
                    if note is not None and note >= GrilleNote.SEUIL_VALIDATION]
        credits_s1 = sum(u['credits'] for u in acquises if u['semestre'] == 1)
        credits_s2 = sum(u['credits'] for u in acquises if u['semestre'] == 2)
        credits = credits_s1 + credits_s2
        reprises = len(ues) - len(acquises)
        # Le fichier compare la moyenne pondérée au seuil de 9,5/20 pour
        # départager VC (validé avec complément) et NV.
        curseur = (Decimal(ligne['total_pondere']) / total_credits
                   if total_credits else Decimal(0))
        attendu = 'V' if not reprises else ('VC' if curseur >= Decimal('9.5')
                                            else 'NV')
        if ligne['credits_s1'] != credits_s1 or ligne['credits_s2'] != credits_s2:
            ecarts.append(f"{ligne['noms']} : crédits validés "
                          f"{ligne['credits_s1']}/{ligne['credits_s2']} "
                          f"au lieu de {credits_s1}/{credits_s2}")
        elif ligne['credits_total'] != credits:
            ecarts.append(f"{ligne['noms']} : total des crédits "
                          f"{ligne['credits_total']} au lieu de {credits}")
        elif ligne['nb_ue_reprendre'] != reprises:
            ecarts.append(f"{ligne['noms']} : UE à reprendre "
                          f"{ligne['nb_ue_reprendre']} au lieu de {reprises}")
        else:
            decision = (ligne['decision'] or '').strip().upper()
            # « A » / « AJ » / « NV » : même verdict d'échec, peu importe
            # la graphie utilisée par la grille.
            meme_echec = attendu == 'NV' and decision in DECISIONS_ECHEC
            if decision and decision != attendu and not meme_echec:
                ecarts.append(f"{ligne['noms']} : décision {ligne['decision']} "
                              f"au lieu de {attendu}")
    if not ecarts:
        return None
    if len(ecarts) == 1:
        return f"Écart de délibération — {ecarts[0]}"
    return (f"Écarts de délibération ({len(ecarts)}) : "
            + ' ; '.join(ecarts[:5]) + ('…' if len(ecarts) > 5 else ''))



def _colonne_de(feuille, libelle, derniere_ligne):
    """Colonne d'un libellé d'en-tête (première occurrence), ou None.

    Les libellés ne sont pas tous sur la même ligne : les intitulés d'UE sont
    sur une ligne, les codes de groupe au-dessus, et la synthèse (« Décision »,
    « Mention »…) encore au-dessus. On balaie donc toutes les lignes d'en-tête
    au lieu de coder en dur des lettres de colonnes.
    """
    cible = slugify(libelle)
    for ligne in range(1, derniere_ligne + 1):
        for colonne in range(1, feuille.max_column + 1):
            if slugify(_texte(feuille.cell(ligne, colonne).value)) == cible:
                return colonne
    return None


def _colonnes_de(feuille, libelle, derniere_ligne):
    """Toutes les colonnes portant ce libellé (triées, sans doublon).

    « Total crédit validé » apparaît deux fois — une par semestre.
    """
    cible = slugify(libelle)
    trouvees = set()
    for ligne in range(1, derniere_ligne + 1):
        for colonne in range(1, feuille.max_column + 1):
            if slugify(_texte(feuille.cell(ligne, colonne).value)) == cible:
                trouvees.add(colonne)
    return sorted(trouvees)


def _groupes_par_colonne(feuille, ligne):
    """Code de groupe (« IBA », « LAN »…) de chaque colonne d'UE.

    Un code couvre plusieurs colonnes via une cellule fusionnée (C6:F6 =
    « IBA ») : sans dépliage, seule la première colonne hériterait du groupe.
    """
    if ligne < 1:
        return {}
    groupes = {}
    for cellule in feuille[ligne]:
        valeur = _texte(cellule.value)
        if valeur and slugify(valeur) != 'groupe':
            groupes[cellule.column] = valeur
    for plage in feuille.merged_cells.ranges:
        # Seules les fusions *posées sur cette ligne* portent des groupes ; les
        # fusions verticales (en-tête d'établissement, signatures…) ne doivent
        # pas écraser un code.
        if plage.min_row == ligne and plage.min_col >= 3:
            valeur = _texte(feuille.cell(plage.min_row, plage.min_col).value)
            for colonne in range(plage.min_col, plage.max_col + 1):
                if valeur:
                    groupes[colonne] = valeur
    return groupes


def _annee_academique(intitule, defaut='2025-2026'):
    """Année académique déduite d'un intitulé (« … 2025 - 2026 »)."""
    annees = re.findall(r'(20[0-9]{2})', _texte(intitule))
    if len(annees) >= 2:
        return f'{annees[0]}-{annees[1]}'
    return defaut
ETABLISSEMENT_DEFAUT = "ÉCOLE SUPÉRIEURE DE FORMATION DES CADRES"


def lire_grille_feuille(feuille):
    """Décompose une grille : (en-tête, UE, lignes d'étudiants).

    Repères repérés dynamiquement (aucune lettre de colonne codée en dur) :
      - la ligne « UNITES D'ENSEIGNEMENT » donne les intitulés d'UE, la ligne
        « Crédits » juste en dessous leurs crédits, la ligne au-dessus les
        codes de groupe ;
      - les libellés de synthèse et les colonnes « Total crédit validé »
        situent les colonnes calculées ;
      - une ligne d'étudiant = un N° d'ordre numérique en colonne A suivi d'un
        nom en colonne B. Les lignes de moyennes, de statistiques (V / VC /
        NV) et de signatures n'ont pas de numéro : elles sont ignorées.

    Les UE sont rattachées au semestre 1 tant qu'on n'a pas atteint la première
    colonne « Total crédit validé » : c'est ce qui sépare les deux semestres
    sans dépendre d'une position fixe.
    """
    ligne_ue = None
    for ligne in range(1, feuille.max_row + 1):
        if _est_en_tete_ue(feuille.cell(ligne, 2).value):
            ligne_ue = ligne
            break
    if ligne_ue is None:
        raise ValueError(
            f"En-tête « {_ENTETE_UE} » introuvable : ce fichier n'a pas le "
            "format d'une grille de délibération (grille LMD attendue).")

    ligne_credits = ligne_ue + 1
    colonnes_credits = _colonnes_de(feuille, 'Total crédit validé', ligne_ue)
    if not colonnes_credits:
        raise ValueError(
            'Colonne « Total crédit validé » introuvable : la grille est '
            'incomplète ou dans un format non pris en charge.')
    groupes = _groupes_par_colonne(feuille, ligne_ue - 1)

    # Les libellés de synthèse ne sont pas toujours au-dessus des UE : dans
    # certaines grilles ils sont posés sur la ligne même des UE (L2 SCF :
    # « Total Général crédit » en AA7 avec 69 en AA8). Ils sont identifiés
    # d'abord et exclus des UE — sinon la colonne deviendrait une fausse UE
    # de 69 crédits, gonflerait le total et fausserait les contrôles de
    # total pondéré et de crédits validés.
    synthese = {
        'credits_s1': colonnes_credits[0],
        'credits_s2': (colonnes_credits[1]
                       if len(colonnes_credits) > 1 else None),
        'credits_total': _colonne_de(feuille, 'Total Général crédit', ligne_ue),
        'nb_ue_reprendre': _colonne_de(feuille, 'Nbre UE à reprendre',
                                       ligne_ue),
        'total_pondere': _colonne_de(feuille, 'Total pondéré', ligne_ue),
        'moyenne': _colonne_de(feuille, 'Moyenne /20', ligne_ue),
        'pourcentage': _colonne_de(feuille, 'Pourcentage', ligne_ue),
        'decision': _colonne_de(feuille, 'Décision', ligne_ue),
        'mention': _colonne_de(feuille, 'Mention', ligne_ue),
    }
    colonnes_synthese = {colonne for colonne in synthese.values() if colonne}

    ues = []
    for colonne in range(3, feuille.max_column + 1):
        intitule = _texte(feuille.cell(ligne_ue, colonne).value)
        if not intitule:
            continue           # colonnes de synthèse, zone annexe
        if colonne in colonnes_synthese or 'total' in intitule.lower():
            continue           # synthèse posée sur la ligne des UE
        credits = _entier(feuille.cell(ligne_credits, colonne).value, None)
        if credits is None:
            continue           # marqueur de fin de tableau (« FIN »)
        ues.append({
            'colonne': colonne,
            'intitule': intitule,
            'credits': credits,
            'semestre': 1 if colonne < colonnes_credits[0] else 2,
            'groupe': groupes.get(colonne, ''),
            'ordre': len(ues) + 1,
        })
    if not ues:
        raise ValueError('Aucune UE lue dans la grille.')

    lignes = []
    for ligne in range(ligne_credits + 1, feuille.max_row + 1):
        rang = _entier(feuille.cell(ligne, 1).value, None)
        noms = _texte(feuille.cell(ligne, 2).value)
        if rang is None or not 0 < rang < 1000 or not noms:
            continue           # en-têtes répétés, totaux, signatures

        def cellule(cle):
            colonne = synthese.get(cle)
            return feuille.cell(ligne, colonne).value if colonne else None

        pourcentage = _note(cellule('pourcentage'))
        lignes.append({
            'rang': rang,
            'noms': noms,
            'notes': {ue['colonne']: _note(
                feuille.cell(ligne, ue['colonne']).value) for ue in ues},
            'credits_s1': _entier(cellule('credits_s1')),
            'credits_s2': _entier(cellule('credits_s2')),
            'credits_total': _entier(cellule('credits_total')),
            'nb_ue_reprendre': _entier(cellule('nb_ue_reprendre')),
            # Le fichier arrondit parfois le total pondéré à l'entier (731 au
            # lieu de 731,2 quand une note est décimale) : le stocker en
            # décimal évite de créer un faux écart à la comparaison.
            'total_pondere': _decimal(cellule('total_pondere')),
            'moyenne': _note(cellule('moyenne')),
            # Le fichier stocke le pourcentage en fraction (0,372) ; on le
            # ramène en points (37,24) pour que l'affichage soit direct.
            'pourcentage': ((pourcentage * 100).quantize(Decimal('0.01'))
                            if pourcentage is not None else None),
            'decision': _texte(cellule('decision')),
            'mention': _texte(cellule('mention')),
        })
    if not lignes:
        raise ValueError("Aucune ligne d'étudiant reconnue dans la grille.")

    etablissement, intitule = '', ''
    for ligne in range(1, ligne_ue):
        valeur = _texte(feuille.cell(ligne, 1).value)
        if not valeur:
            continue
        if 'grille' in slugify(valeur):
            intitule = valeur
        elif not etablissement:
            etablissement = valeur
    # L'en-tête des relevés porte le nom officiel de l'école : l'acronyme du
    # fichier (« ESFORCA/INPP ») est normalisé, et l'absence d'en-tête retombe
    # sur le nom complet plutôt que sur une valeur vide.
    if not etablissement or 'esforca' in slugify(etablissement):
        etablissement = ETABLISSEMENT_DEFAUT

    entete = {
        'etablissement': etablissement[:100],
        'intitule': intitule[:200],
        'annee_academique': _annee_academique(intitule),
    }
    return entete, ues, lignes
def import_grille_excel(file_obj, promotion, session=None,
                        annee_academique=None):
    """Importe une grille de délibération pour `promotion`.

    Retourne le même dictionnaire de résultat que les autres imports
    (`success`, `created`, `updated`, `skipped`, `errors`), enrichi du
    récapitulatif de la grille (`grille`, `nb_ue`, `nb_lignes`, `nb_notes`).

    Le bouton « Importer » *remplace* : toutes les grilles antérieures de la
    promotion pour cette année académique sont supprimées — quelle que soit
    leur session, y compris sans session — avant d'écrire la nouvelle. Tout
    est dérivé du fichier ; garder une ancienne version (UE, notes,
    délibération) à côté de la nouvelle laisserait des données périmées.
    Les notes sont insérées par lots (`bulk_create`) : une grille de 30 UE ×
    1 000 étudiants représente 30 000 lignes, qu'une insertion unitaire
    rendrait impraticable.
    """
    resultat = {
        'success': False, 'created': 0, 'updated': 0, 'skipped': 0,
        'rattaches_hors_promotion': 0,
        'errors': [], 'grille': None, 'nb_ue': 0, 'nb_lignes': 0, 'nb_notes': 0,
    }
    if promotion is None:
        resultat['errors'].append('Promotion obligatoire pour une grille.')
        return resultat

    try:
        classeur = openpyxl.load_workbook(file_obj, data_only=True)
        entete, ues, lignes = lire_grille_feuille(classeur.worksheets[0])
    except Exception as err:               # fichier illisible ou format autre
        resultat['errors'].append(
            f'Fichier illisible ou format inattendu : {err}')
        return resultat

    annee = annee_academique or entete['annee_academique']

    with transaction.atomic():
        # Purge complète des anciennes grilles de la promotion pour cette
        # année (UE, lignes et notes supprimées en cascade) — pas seulement
        # celle de la session courante : un fichier grille est la source de
        # vérité annuelle de la promotion, aucune version antérieure ne doit
        # lui survivre.
        supprimees, _ = Grille.objects.filter(
            promotion=promotion, annee_academique=annee).delete()
        resultat['updated'] = 1 if supprimees else 0
        resultat['created'] = 0 if supprimees else 1

        grille = Grille.objects.create(
            promotion=promotion, session=session, annee_academique=annee,
            etablissement=entete['etablissement'], intitule=entete['intitule'],
            total_credits=sum(ue['credits'] for ue in ues),
            fichier_source=getattr(file_obj, 'name', '') or '',
        )

        # UE -> cours du référentiel : les intitulés des cours ont été alignés
        # sur ceux de la grille (manage.py aligner_cours_grille), un simple
        # slug suffit donc à les apparier.
        cours_par_slug = {}
        for cours in Cours.objects.filter(promotion=promotion):
            cours_par_slug.setdefault(slugify(cours.nom), cours)

        ue_par_colonne = {}
        for ue in ues:
            ue_par_colonne[ue['colonne']] = GrilleUE.objects.create(
                grille=grille, cours=cours_par_slug.get(slugify(ue['intitule'])),
                intitule=ue['intitule'], credits=ue['credits'],
                semestre=ue['semestre'], groupe=ue['groupe'], ordre=ue['ordre'])

        # Étudiants : le N° d'ordre de la grille correspond au matricule
        # déterministe « <PROMO>-<rang> » posé par l'import des étudiants. Le
        # nom sert de repli si la grille a été renumérotée entre-temps.
        # Repli élargi : quand une grille d'année antérieure (ex. L1 2024-2025)
        # est importée après le passage en L2, les fiches ont déjà changé de
        # promotion. On cherche alors par nom exact dans toute la base, sans
        # déplacer la fiche : la ligne de grille reste rattachée à la fiche
        # actuelle, et l'étape de parcours de la promotion quittée s'y inscrit
        # — c'est ce qui donne aux L2/L3 leur historique des années antérieures.
        promo_slug = slugify(promotion.nom).upper() or 'ETU'
        etudiants = list(Etudiant.objects.filter(promotion=promotion))
        par_numero = {e.numero_etudiant: e for e in etudiants}
        par_nom = {e.noms: e for e in etudiants}
        par_nom_global = {}
        for etudiant in Etudiant.objects.exclude(promotion=promotion):
            par_nom_global.setdefault(etudiant.noms, []).append(etudiant)

        resultat['rattaches_hors_promotion'] = 0
        notes = []
        for ligne in lignes:
            etudiant = par_numero.get(f'{promo_slug}-{ligne["rang"]:03d}')
            if etudiant is None:
                etudiant = par_nom.get(ligne['noms'])
            if etudiant is None:
                candidats = par_nom_global.get(ligne['noms'], [])
                if len(candidats) == 1:
                    etudiant = candidats[0]
                    resultat['rattaches_hors_promotion'] += 1
                elif len(candidats) > 1:
                    resultat['skipped'] += 1
                    resultat['errors'].append(
                        f"Ligne {ligne['rang']} — « {ligne['noms']} » : "
                        f'nom ambigu ({len(candidats)} fiches hors '
                        f'{promotion.nom}), ligne ignorée.')
                    continue
            if etudiant is None:
                resultat['skipped'] += 1
                resultat['errors'].append(
                    f"Ligne {ligne['rang']} — « {ligne['noms']} » : étudiant "
                    f'introuvable dans {promotion.nom}, ligne ignorée.')
                continue

            ligne_grille = GrilleEtudiant.objects.create(
                grille=grille, etudiant=etudiant, rang=ligne['rang'],
                credits_s1=ligne['credits_s1'], credits_s2=ligne['credits_s2'],
                credits_total=ligne['credits_total'],
                nb_ue_reprendre=ligne['nb_ue_reprendre'],
                total_pondere=ligne['total_pondere'],
                moyenne=ligne['moyenne'], pourcentage=ligne['pourcentage'],
                decision=ligne['decision'][:2], mention=ligne['mention'][:20],
            )
            # Étape de parcours : l'étudiant a fréquenté cette promotion cette
            # année-là. C'est ce qui constitue, pour un étudiant de L2 ou de
            # L3, l'historique de ses années antérieures — renseigné à chaque
            # import de grille, jamais saisi à la main.
            HistoriquePromotion.enregistrer(etudiant, promotion, annee, grille)
            for colonne, note in ligne['notes'].items():
                notes.append(GrilleNote(ligne=ligne_grille,
                                        ue=ue_par_colonne[colonne], note=note))

        GrilleNote.objects.bulk_create(notes)

        resultat.update({
            'success': True, 'grille': grille, 'nb_ue': len(ues),
            'nb_lignes': len(lignes) - resultat['skipped'],
            'nb_notes': len(notes),
        })
        resultat['errors'].extend(_controles_grille(ues, lignes,
                                                   grille.total_credits))
    return resultat


def _controles_grille(ues, lignes, total_credits):
    """Contrôles d'intégrité d'une grille importée (avertissements)."""
    alertes = []
    for controle in (_alerte_credits(lignes, ues),
                     _alerte_decisions(lignes, ues, total_credits)):
        if controle:
            alertes.append(controle)
    return alertes
