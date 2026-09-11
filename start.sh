#!/bin/bash

# ============================================================
#  start.sh — Lanceur de l'application "Gestion des examens"
#  Utilisable comme raccourci depuis le Bureau (macOS).
#
#  - Se place automatiquement dans le dossier du projet,
#    quel que soit le répertoire depuis lequel il est lancé.
#  - Applique les migrations, ouvre le navigateur et démarre
#    le serveur Django sur http://127.0.0.1:8000/
# ============================================================

# Chemin absolu du dossier contenant ce script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

PORT=8000
URL="http://127.0.0.1:${PORT}/"

# Autoriser la désactivation de l'ouverture auto du navigateur :
#   OPEN_BROWSER=0 ./start.sh   → lance sans ouvrir le navigateur
OPEN_BROWSER="${OPEN_BROWSER:-1}"

echo "🚀 Lancement de l'application \"Gestion des examens\"..."

# --- Vérification de la présence de manage.py ---
if [ ! -f "manage.py" ]; then
    echo "❌ Erreur : manage.py introuvable dans $SCRIPT_DIR"
    echo "   Vérifiez que le raccourci pointe vers le bon projet."
    read -r -p "Appuyez sur Entrée pour fermer..."
    exit 1
fi

# --- Choix de l'interpréteur Python (virtualenv prioritaire) ---
if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    PYTHON="$SCRIPT_DIR/.venv/bin/python"
else
    PYTHON="python3"
fi

# --- Sécurité : vérification que le port est libre ---
if lsof -ti "tcp:${PORT}" >/dev/null 2>&1; then
    echo "⚠️  Un serveur tourne déjà sur le port ${PORT}."
    if [ "$OPEN_BROWSER" = "1" ]; then
        echo "   J'ouvre le navigateur sur l'instance en cours..."
        open "$URL"
    fi
    read -r -p "Appuyez sur Entrée pour fermer..."
    exit 0
fi

# --- Mise à jour de la base de données ---
echo "🔄 Mise à jour de la base de données..."
"$PYTHON" manage.py migrate
if [ $? -ne 0 ]; then
    echo "❌ Erreur lors des migrations. Vérifiez la base de données."
    read -r -p "Appuyez sur Entrée pour fermer..."
    exit 1
fi

# --- Ouverture automatique du navigateur après un court délai ---
if [ "$OPEN_BROWSER" = "1" ]; then
    ( sleep 2 && open "$URL" ) &
fi

echo "🌐 Démarrage du serveur sur $URL"
echo "   (Ctrl+C pour arrêter le serveur)"
"$PYTHON" manage.py runserver "127.0.0.1:${PORT}"

# Le serveur étant arrêté, on laisse la fenêtre lisible
read -r -p "Serveur arrêté. Appuyez sur Entrée pour fermer..."
