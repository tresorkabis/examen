import os
import django
import pandas as pd
import re
import unicodedata
from pathlib import Path

# Configuration de l'environnement Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Promotion, Enseignant, Cours

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_FILE_PATH = BASE_DIR / 'data' / 'Charge_Horaire_2025_2026.xlsx'


def slugify(text):
    text = unicodedata.normalize('NFKD', str(text))
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'[^a-z0-9]', '', text.lower())


def teacher_email(full_name):
    parts = [slugify(part) for part in str(full_name).split()]
    base = '.'.join(part for part in parts if part) or 'enseignant'
    return f'{base}@example.com'

def import_excel_data(file_path):
    print(f"🚀 Démarrage de l'importation depuis : {file_path}")
    
    # Feuille à importer
    sheets_to_import = ['LICENCE']
    
    try:
        excel_file = pd.ExcelFile(file_path)
        
        for sheet_name in sheets_to_import:
            if sheet_name not in excel_file.sheet_names:
                print(f"⚠️ Feuille {sheet_name} non trouvée, passage à la suivante.")
                continue
                
            print(f"\n--- Importation de la feuille : {sheet_name} ---")
            df = pd.read_excel(file_path, sheet_name=sheet_name)
            
            # Nettoyage basique : supprimer les lignes où les colonnes clés sont vides
            df = df.dropna(subset=['ENSEIGNANT', 'COURS', 'PROMOTION'])
            
            # Filtrer les lignes de total (ex: "Total BABANEMI")
            df = df[~df['ENSEIGNANT'].astype(str).str.contains('Total', case=False)]
            
            count_imported = 0
            for index, row in df.iterrows():
                try:
                    # 1. Gestion de la Promotion
                    promo_name = str(row['PROMOTION']).strip()
                    promotion, _ = Promotion.objects.get_or_create(nom=promo_name)
                    
                    # 2. Gestion de l'Enseignant
                    # On suppose que le nom est dans la colonne 'ENSEIGNANT'
                    # On sépare nom et prénom si possible, sinon on met tout dans le nom
                    full_name = str(row['ENSEIGNANT']).strip()
                    name_parts = full_name.split(' ', 1)
                    nom = name_parts[0]
                    prenom = name_parts[1] if len(name_parts) > 1 else ""
                    
                    enseignant, _ = Enseignant.objects.get_or_create(
                        email=teacher_email(full_name),
                        defaults={'nom': nom, 'prenom': prenom}
                    )
                    
                    # 3. Gestion du Cours
                    cours_nom = str(row['COURS']).strip()
                    cours = (Cours.objects
                             .filter(nom=cours_nom, promotion=promotion)
                             .order_by('id')
                             .first())
                    created = cours is None
                    if created:
                        cours = Cours.objects.create(
                            nom=cours_nom,
                            promotion=promotion,
                            enseignant=enseignant,
                            coefficient=1,
                        )
                    elif cours.enseignant_id != enseignant.id:
                        cours.enseignant = enseignant
                        cours.save(update_fields=['enseignant'])
                    
                    count_imported += 1
                except Exception as e:
                    print(f"❌ Erreur à la ligne {index + 2} : {e}")
            
            print(f"✅ {count_imported} cours importés depuis {sheet_name}.")

        print("\n✨ Importation terminée avec succès !")

    except Exception as e:
        print(f"💥 Erreur critique lors de l'importation : {e}")

if __name__ == "__main__":
    FILE_PATH = DEFAULT_FILE_PATH
    import_excel_data(FILE_PATH)
