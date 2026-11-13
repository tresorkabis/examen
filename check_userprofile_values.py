import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth.models import User
from app.models import UserProfile

user = User.objects.get(username='kabisayi_tresor')
profile, created = UserProfile.objects.get_or_create(user=user)
print("UserProfile for", user.username)
print("  role:", profile.role)
print("  enseignant:", profile.enseignant)