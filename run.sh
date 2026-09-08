#!/bin/zsh

# Script pour lancer l'application Django
echo "🚀 Lancement de l'application de gestion des examens..."

# Vérification de l'existence du fichier manage.py
if [ ! -f "manage.py" ]; then
    echo "❌ Erreur : manage.py non trouvé dans le répertoire courant."
    exit 1
fi

# Application des migrations pour s'assurer que la DB est à jour
echo "🔄 Mise à jour de la base de données..."
python manage.py migrate

# Lancement du serveur
echo "🌐 Démarrage du serveur sur http://127.0.0.1:8000/"
python manage.py runserver
