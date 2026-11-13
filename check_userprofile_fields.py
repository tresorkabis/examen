import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import UserProfile

fields = [f.name for f in UserProfile._meta.get_fields()]
print("UserProfile fields:", fields)