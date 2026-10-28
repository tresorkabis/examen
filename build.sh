# Création du virtualenv si nécessaire
if [ ! -d ".venv" ]; then
    python3.12 -m venv .venv
fi

# Installation des dépendances dans le virtualenv local
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt

# Collecte des fichiers statiques
python manage.py collectstatic --noinput

# Application des migrations (si nécessaire/possible sur Vercel)
# python manage.py migrate --noinput
