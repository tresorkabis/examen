"""
Modèle de configuration WSGI pour PythonAnywhere.

À copier-coller dans votre fichier WSGI sur PythonAnywhere :
Web tab -> Configuration file -> /var/www/<votre_username>_pythonanywhere_com_wsgi.py

Remplacez '<votre_username>' par votre véritable identifiant PythonAnywhere.
"""

import os
import sys

# 1. Chemin vers la racine du projet sur PythonAnywhere
# (adaptez 'examen' si vous avez cloné sous un autre nom de dossier)
project_home = '/home/<votre_username>/examen'

if project_home not in sys.path:
    sys.path.insert(0, project_home)

# 2. Configuration du module de settings Django
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'

# 3. Variables d'environnement de production
# Activez DEBUG=False en production
os.environ['DJANGO_DEBUG'] = 'False'

# Clé secrète de production (générez une chaîne aléatoire ou conservez la vôtre)
# os.environ['DJANGO_SECRET_KEY'] = 'une-cle-secrete-aleatoire-et-complexe'

# Domaines autorisés (ex: 'votre_username.pythonanywhere.com,www.votredomaine.com')
# os.environ['ALLOWED_HOSTS'] = '<votre_username>.pythonanywhere.com'

# Origines CSRF autorisées (ex: 'https://<votre_username>.pythonanywhere.com')
# os.environ['CSRF_TRUSTED_ORIGINS'] = 'https://<votre_username>.pythonanywhere.com'

# 4. Exposition de l'application WSGI pour le serveur web
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
