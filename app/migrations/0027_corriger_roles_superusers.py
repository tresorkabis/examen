from django.db import migrations


def corriger_roles_superusers(apps, schema_editor):
    """Met à jour le rôle des profils liés aux superusers Django.

    Un superuser doit avoir le rôle ``admin`` dans son profil pour que le
    stockage en base soit cohérent avec le rôle effectif renvoyé par
    ``get_user_role()`` (le ``is_superuser`` prime sur le rôle métier).
    """
    User = apps.get_model('auth', 'User')
    UserProfile = apps.get_model('app', 'UserProfile')

    superusers = User.objects.filter(is_superuser=True)
    for profile in UserProfile.objects.filter(user__in=superusers):
        if profile.role != 'admin':
            profile.role = 'admin'
            profile.save(update_fields=['role'])


def inverse_corriger_roles_superusers(apps, schema_editor):
    """Inverse : remet le rôle par défaut ``enseignant`` pour les profils."""
    UserProfile = apps.get_model('app', 'UserProfile')
    for profile in UserProfile.objects.filter(role='admin'):
        profile.role = 'enseignant'
        profile.save(update_fields=['role'])


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0026_inscription_moyenne_saisie_alter_inscription_note'),
    ]

    operations = [
        migrations.RunPython(corriger_roles_superusers, inverse_corriger_roles_superusers),
    ]