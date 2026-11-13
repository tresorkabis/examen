import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Enseignant

fields = [f.name for f in Enseignant._meta.get_fields()]
print("Enseignant fields:", fields)