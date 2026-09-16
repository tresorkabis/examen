import re
import unicodedata
import pandas as pd
from django.db import transaction
from app.models import Etudiant, Enseignant, Cours, Promotion

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
