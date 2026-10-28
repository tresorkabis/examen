#!/bin/zsh

# Script pour lancer l'application Django
echo "🚀 Lancement de l'application de gestion des examens..."

# Vérification de l'existence du fichier manage.py
if [ ! -f "manage.py" ]; then
    echo "❌ Erreur : manage.py non trouvé dans le répertoire courant."
    exit 1
fi

# Préférence: virtualenv local, puis Python 3.12, puis Python 3/ système.
if [ -x ".venv/bin/python" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3.12 >/dev/null 2>&1; then
    PYTHON="python3.12"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
else
    PYTHON="python"
fi

# Application des migrations pour s'assurer que la DB est à jour
echo "🔄 Mise à jour de la base de données..."
"$PYTHON" manage.py migrate

# Lancement du serveur
echo "🌐 Démarrage du serveur sur http://127.0.0.1:8000/"
"$PYTHON" manage.py runserver
