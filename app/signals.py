"""Signaux : invalide le cache des compteurs du tableau de bord.

Les compteurs (étudiants, cours, sessions, enseignants, examens, promos) sont
mis en cache 60 s et dépendent de la session en cours ; toute écriture sur les
modèles concernés (y compris examens et inscriptions, qui restreignent les
compteurs à la session active) purge la clé pour que le tableau de bord reste
exact.
"""

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import (Cours, Enseignant, Etudiant, Examen, Inscription, Session,
                     UserProfile)

CLE_COMPTEURS = 'dashboard_compteurs'


@receiver([post_save, post_delete],
          sender=Etudiant, dispatch_uid='app.purge_dashboard_etudiant')
@receiver([post_save, post_delete],
          sender=Cours, dispatch_uid='app.purge_dashboard_cours')
@receiver([post_save, post_delete],
          sender=Enseignant, dispatch_uid='app.purge_dashboard_enseignant')
@receiver([post_save, post_delete],
          sender=Session, dispatch_uid='app.purge_dashboard_session')
@receiver([post_save, post_delete],
          sender=Examen, dispatch_uid='app.purge_dashboard_examen')
@receiver([post_save, post_delete],
          sender=Inscription, dispatch_uid='app.purge_dashboard_inscription')
def purger_compteurs_dashboard(sender, **kwargs):
    cache.delete(CLE_COMPTEURS)


@receiver([post_save], sender=User, dispatch_uid='app.create_user_profile')
def create_user_profile(sender, instance, created, **kwargs):
    """S'assurer qu'un profil existe pour chaque utilisateur."""
    if created:
        try:
            UserProfile.objects.get(user=instance)
        except UserProfile.DoesNotExist:
            UserProfile.objects.create(user=instance)
