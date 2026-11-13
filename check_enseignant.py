import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth.models import User
from app.models import Enseignant

user = User.objects.get(username='kabisayi_tresor')
print("User:", user.username, user.email)

enseignant = Enseignant.objects.get(user=user)
print("\nEnseignant:")
print("  Nom:", enseignant.nom)
print("  Prénom:", enseignant.prenom)
print("  Email:", enseignant.email)