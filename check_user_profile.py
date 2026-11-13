import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Enseignant

field = Enseignant._meta.get_field("user_profile")
print("user_profile field:", field)
print("related model:", field.related_model)