import re
import unicodedata
import pandas as pd
from django.db import transaction
from app.models import Etudiant, Enseignant, Cours, Promotion

NUM_RE = re.compile(r'^\d{1,3}$')

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


def generate_unique_student_email(nom, prenom):
    """Génère un email unique pour un étudiant."""
    base = f"{slugify(prenom)}.{slugify(nom)}" or 'etudiant'
    email = f"{base}@example.com"
    n = 2
    while Etudiant.objects.filter(email=email).exists():
        email = f"{base}{n}@example.com"
        n += 1
    return email


def generate_unique_teacher_email(full_name):
    """Génère un email unique pour un enseignant à partir de son nom complet."""
    parts = [slugify(part) for part in str(full_name).split()]
    base = '.'.join(part for part in parts if part) or 'enseignant'
    email = f"{base}@example.com"
    n = 2
    while Enseignant.objects.filter(email=email).exists():
        email = f"{base}{n}@example.com"
        n += 1
    return email


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

                        # Promotion
                        promo_obj = default_promotion
                        if promo_col and not pd.isna(row[promo_col]):
                            promo_name = normalize_promotion_name(str(row[promo_col]).strip())
                            if promo_name:
                                promo_obj, _ = Promotion.objects.get_or_create(nom=promo_name)

                        if not promo_obj:
                            # Utiliser le nom de la feuille comme promotion si valide
                            if sheet_name and not sheet_name.lower().startswith('sheet'):
                                promo_obj, _ = Promotion.objects.get_or_create(nom=normalize_promotion_name(sheet_name.strip()))
                            else:
                                promo_obj, _ = Promotion.objects.get_or_create(nom='Promotion Générale')

                        # Numéro étudiant (optionnel, généré par le modèle si absent)
                        numero = str(row[numero_col]).strip() if numero_col and not pd.isna(row[numero_col]) else None

                        # Email (optionnel)
                        email = str(row[email_col]).strip() if email_col and not pd.isna(row[email_col]) else None

                        # Enregistrement
                        if numero:
                            etu, created = Etudiant.objects.get_or_create(
                                numero_etudiant=numero,
                                defaults={'noms': noms_val, 'email': email, 'promotion': promo_obj}
                            )
                        else:
                            etu, created = Etudiant.objects.get_or_create(
                                noms=noms_val,
                                promotion=promo_obj,
                                defaults={'email': email}
                            )

                        if created:
                            created_count += 1
                        else:
                            etu.noms = noms_val
                            etu.promotion = promo_obj
                            if email:
                                etu.email = email
                            etu.save()
                            updated_count += 1

                    except Exception as row_err:
                        errors.append(f"Feuille '{sheet_name}', ligne {line_num} : {row_err}")

            else:
                # Mode grille de délibération LMD (col 0 = num d'ordre, col 1 = nom complet)
                df_raw = excel_file.parse(sheet_name, header=None)
                promo_obj = default_promotion
                if not promo_obj:
                    promo_obj, _ = Promotion.objects.get_or_create(nom=normalize_promotion_name(sheet_name.strip()))

                promo_slug = slugify(promo_obj.nom).upper() or 'ETU'
                seen_names = set()

                for idx in range(df_raw.shape[0]):
                    try:
                        val_num = str(df_raw.iat[idx, 0]).strip() if not pd.isna(df_raw.iat[idx, 0]) else ''
                        val_name = str(df_raw.iat[idx, 1]).strip() if df_raw.shape[1] > 1 and not pd.isna(df_raw.iat[idx, 1]) else ''

                        if not NUM_RE.match(val_num) or not val_name:
                            continue

                        val_name = re.sub(r'\s+', ' ', val_name)
                        if val_name.upper() in seen_names:
                            skipped_count += 1
                            continue
                        seen_names.add(val_name.upper())

                        noms_val = val_name
                        numero = f"{promo_slug}-{int(val_num):03d}"

                        etu, created = Etudiant.objects.get_or_create(
                            numero_etudiant=numero,
                            defaults={'noms': noms_val, 'email': None, 'promotion': promo_obj}
                        )
                        if created:
                            created_count += 1
                        else:
                            etu.noms = noms_val
                            etu.promotion = promo_obj
                            etu.save()
                            updated_count += 1

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
                    elif full_name_col and not pd.isna(row[full_name_col]):
                        full_name = str(row[full_name_col]).strip()
                        if 'total' in full_name.lower():
                            skipped_count += 1
                            continue
                        parts = full_name.split(' ', 1)
                        nom = parts[0]
                        prenom = parts[1] if len(parts) > 1 else ''
                    else:
                        skipped_count += 1
                        continue

                    if not nom:
                        skipped_count += 1
                        continue

                    if email_col and not pd.isna(row[email_col]):
                        email = str(row[email_col]).strip()
                    else:
                        full_name_str = f"{nom} {prenom}".strip()
                        email = generate_unique_teacher_email(full_name_str)

                    ens, created = Enseignant.objects.get_or_create(
                        email=email,
                        defaults={'nom': nom, 'prenom': prenom}
                    )
                    if created:
                        created_count += 1
                    else:
                        ens.nom = nom
                        ens.prenom = prenom
                        ens.save()
                        updated_count += 1

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

                    # Promotion
                    promo_obj = default_promotion
                    if promo_col and not pd.isna(row[promo_col]):
                        promo_name = normalize_promotion_name(str(row[promo_col]).strip())
                        if promo_name:
                            promo_obj, _ = Promotion.objects.get_or_create(nom=promo_name)

                    if not promo_obj:
                        if sheet_name and not sheet_name.lower().startswith('sheet'):
                            promo_obj, _ = Promotion.objects.get_or_create(nom=normalize_promotion_name(sheet_name.strip()))
                        else:
                            promo_obj, _ = Promotion.objects.get_or_create(nom='Promotion Générale')


                    # Enseignant
                    enseignant_obj = None
                    if ens_col and not pd.isna(row[ens_col]):
                        full_name = str(row[ens_col]).strip()
                        if full_name and 'total' not in full_name.lower():
                            parts = full_name.split(' ', 1)
                            nom = parts[0]
                            prenom = parts[1] if len(parts) > 1 else ''
                            email = generate_unique_teacher_email(full_name)
                            enseignant_obj, _ = Enseignant.objects.get_or_create(
                                email=email,
                                defaults={'nom': nom, 'prenom': prenom}
                            )

                    # Coefficient
                    coefficient = 1
                    if coeff_col and not pd.isna(row[coeff_col]):
                        try:
                            coefficient = int(row[coeff_col])
                        except (ValueError, TypeError):
                            coefficient = 1

                    cours = Cours.objects.filter(nom=cours_nom, promotion=promo_obj).first()
                    if cours is None:
                        Cours.objects.create(
                            nom=cours_nom,
                            promotion=promo_obj,
                            enseignant=enseignant_obj,
                            coefficient=coefficient
                        )
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
