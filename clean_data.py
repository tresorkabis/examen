import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Cours, Enseignant, Promotion

def clean_data():
    print("🧹 Nettoyage des données...")
    Cours.objects.all().delete()
    Enseignant.objects.all().delete()
    Promotion.objects.all().delete()
    print("✅ Base de données nettoyée.")

if __name__ == "__main__":
    clean_data()
