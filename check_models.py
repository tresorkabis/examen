import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Enseignant, Etudiant, Promotion, Mention, Filiere, Cours

print("Enseignant count:", Enseignant.objects.count())
print("Etudiant count:", Etudiant.objects.count())
print("Promotion count:", Promotion.objects.count())
print("Mention count:", Mention.objects.count())
print("Filiere count:", Filiere.objects.count())
print("Cours count:", Cours.objects.count())

# Check if kabisayi_tresor is an Enseignant
from django.contrib.auth.models import User
try:
    user = User.objects.get(username='kabisayi_tresor')
    print("\nUser found:", user.username)
    
    # Try to get related Enseignant
    try:
        enseignant = Enseignant.objects.get(user=user)
        print("Enseignant linked:", enseignant)
    except Enseignant.DoesNotExist:
        print("No Enseignant linked to this user")
    
    # Try to get related Etudiant
    try:
        etudiant = Etudiant.objects.get(user=user)
        print("Etudiant linked:", etudiant)
    except Etudiant.DoesNotExist:
        print("No Etudiant linked to this user")
except User.DoesNotExist:
    print("User not found")