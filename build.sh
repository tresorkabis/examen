# Installation des dépendances
pip install -r requirements.txt

# Collecte des fichiers statiques
python manage.py collectstatic --noinput

# Application des migrations (si nécessaire/possible sur Vercel)
# python manage.py migrate --noinput
