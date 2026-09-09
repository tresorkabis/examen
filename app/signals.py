"""Signaux : invalide le cache des compteurs du dashboard.

Les compteurs (étudiants, cours, sessions, enseignants) sont mis en cache
60 s ; toute création / modification / suppression de ces modèles purge la
clé pour que le tableau de bord reste exact.
"""

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Cours, Enseignant, Etudiant, Session

CLE_COMPTEURS = 'dashboard_compteurs'


@receiver([post_save, post_delete],
          sender=Etudiant, dispatch_uid='app.purge_dashboard_etudiant')
@receiver([post_save, post_delete],
          sender=Cours, dispatch_uid='app.purge_dashboard_cours')
@receiver([post_save, post_delete],
          sender=Enseignant, dispatch_uid='app.purge_dashboard_enseignant')
@receiver([post_save, post_delete],
          sender=Session, dispatch_uid='app.purge_dashboard_session')
def purger_compteurs_dashboard(sender, **kwargs):
    cache.delete(CLE_COMPTEURS)
