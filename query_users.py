import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth.models import User

users = User.objects.all()
print("Total users:", users.count())
for u in users:
    print(f"Username: {u.username}, Email: {u.email}, is_staff: {u.is_staff}, is_superuser: {u.is_superuser}")