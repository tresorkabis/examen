import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.conf import settings
print("AUTH_USER_MODEL:", getattr(settings, "AUTH_USER_MODEL", "Not set"))
print("INSTALLED_APPS:", settings.INSTALLED_APPS)