# Guide de Déploiement sur PythonAnywhere — ExamManager

Ce guide détaille pas à pas la mise en production de l'application **ExamManager** sur [PythonAnywhere](https://www.pythonanywhere.com/).

---

## 📋 Prérequis

1. Un compte sur [PythonAnywhere](https://www.pythonanywhere.com/) (compte *Beginner* gratuit ou payant).
2. Votre nom d'utilisateur PythonAnywhere (noté `<username>` dans ce guide).
3. Votre dépôt Git (GitHub, GitLab, ou archives des fichiers).

---

## Étape 1 : Ouvrir une console Bash

1. Connectez-vous sur votre tableau de bord PythonAnywhere.
2. Cliquez sur l'onglet **Consoles**.
3. Lancez une console **Bash**.

---

## Étape 2 : Récupérer le projet

Dans la console Bash, clonez votre dépôt dans votre dossier personnel :

```bash
cd /home/<username>
git clone <URL_DE_VOTRE_DEPOT_GIT> examen
cd examen
```

*(Si vous n'utilisez pas Git, vous pouvez transférer les fichiers via l'onglet **Files** ou une archive zip).*

---

## Étape 3 : Créer l'environnement virtuel Python

PythonAnywhere fournit l'outil `virtualenvwrapper`. Créez un environnement virtuel (recommandé : Python 3.12 ou 3.13) :

```bash
mkvirtualenv --python=python3.12 examen-venv
```

*(Une fois créé, l'environnement virtuel est automatiquement activé. Si vous ouvrez une nouvelle console plus tard, réactivez-le avec `workon examen-venv`).*

---

## Étape 4 : Installer les dépendances

Installez toutes les bibliothèques du projet listées dans `requirements.txt` :

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

## Étape 5 : Initialiser la base de données et les fichiers statiques

Toujours dans le dossier `examen` avec le virtualenv activé :

```bash
# 1. Appliquer les migrations de la base de données SQLite
python manage.py migrate

# 2. Collecter tous les fichiers statiques (CSS, JS, icônes)
python manage.py collectstatic --noinput

# 3. Créer le compte administrateur Django
python manage.py createsuperuser
```

---

## Étape 6 : Configurer l'application Web sur PythonAnywhere

Rendez-vous sur l'onglet **Web** de PythonAnywhere :

### 1. Créer une nouvelle Web App
- Cliquez sur **Add a new web app**.
- Choisissez votre domaine (ex. `<username>.pythonanywhere.com`).
- Choisissez **Manual configuration** (ne choisissez pas Django automatique, car votre projet existe déjà).
- Sélectionnez la version de Python correspondant à votre environnement (ex. **Python 3.12**).

### 2. Configurer les chemins du projet
Dans la section **Code** :
- **Source code** : `/home/<username>/examen`
- **Working directory** : `/home/<username>/examen`

### 3. Configurer l'environnement virtuel
Dans la section **Virtualenv** :
- Chemin : `/home/<username>/.virtualenvs/examen-venv`

### 4. Configurer les fichiers statiques (Static Files)
Dans la section **Static files**, ajoutez une ligne de mappage :
- **URL** : `/static/`
- **Directory** : `/home/<username>/examen/staticfiles`

---

## Étape 7 : Configurer le fichier WSGI

Dans l'onglet **Web**, section **Code**, cliquez sur le lien du fichier **WSGI configuration file** (nommé `/var/www/<username>_pythonanywhere_com_wsgi.py`).

1. Effacez tout le contenu par défaut de ce fichier.
2. Copiez-y le contenu du fichier `deploy/pythonanywhere_wsgi.py` (ou le code ci-dessous) :

```python
import os
import sys

# 1. Chemin vers votre projet
project_home = '/home/<username>/examen'
if project_home not in sys.path:
    sys.path.insert(0, project_home)

# 2. Module de settings Django
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'

# 3. Paramètres de production
os.environ['DJANGO_DEBUG'] = 'False'
# Remplacez par une clé secrète robuste (optionnel mais recommandé) :
# os.environ['DJANGO_SECRET_KEY'] = 'votre-cle-secrete-production'

# 4. Démarrage de l'application
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
```

*(N'oubliez pas de remplacer `<username>` par votre identifiant PythonAnywhere).*
3. Cliquez sur **Save** en haut à droite.

---

## Étape 8 : Recharger l'application et tester

1. Retournez dans l'onglet **Web**.
2. Cliquez sur le gros bouton vert **Reload <username>.pythonanywhere.com**.
3. Ouvrez votre site : `https://<username>.pythonanywhere.com/`.

✅ **Votre application ExamManager est maintenant en ligne !**

---

## 🛠️ Dépannage & Commandes utiles

- **Voir les erreurs en cas de problème** :
  Consultez les fichiers de log dans l'onglet **Web** :
  - **Error log** : `/var/log/<username>.pythonanywhere.com.error.log`
  - **Server log** : `/var/log/<username>.pythonanywhere.com.server.log`

- **Mettre à jour le code après une modification Git** :
  ```bash
  cd /home/<username>/examen
  git pull
  workon examen-venv
  python manage.py migrate
  python manage.py collectstatic --noinput
  ```
  Puis cliquez sur **Reload** dans l'onglet **Web**.
