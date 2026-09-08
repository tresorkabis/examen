import pandas as pd

file_path = '/Users/tresorkabis/dev/examen/Charge_Horaire_2025_2026.xlsx'

try:
    # Lire toutes les feuilles du fichier
    excel_file = pd.ExcelFile(file_path)
    print(f"Feuilles trouvées : {excel_file.sheet_names}\n")

    for sheet_name in excel_file.sheet_names:
        print(f"--- Analyse de la feuille : {sheet_name} ---")
        df = pd.read_excel(file_path, sheet_name=sheet_name)
        print(f"Colonnes : {df.columns.tolist()}")
        print("\n5 premières lignes :")
        print(df.head())
        print("\n" + "="*50 + "\n")

except Exception as e:
    print(f"Erreur lors de la lecture du fichier : {e}")
