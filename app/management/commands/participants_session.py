"""
Liste les participants (étudiants inscrits) d'une session d'examens.

Chaque examen appartient à une session ; la liste des participants d'un
examen est donnée par les inscriptions (modèle Inscription).

Usage :
    python manage.py participants_session --session 4
    python manage.py participants_session --session "Rattrapage SEMESTRE 2 2025 - 2026"
    python manage.py participants_session --examen 22        # un examen précis
"""
from django.core.management.base import BaseCommand, CommandError
from django.shortcuts import get_object_or_404

from app.models import Examen, Session


class Command(BaseCommand):
    help = "Liste les participants (étudiants inscrits) d'une session d'examens."

    def add_arguments(self, parser):
        parser.add_argument(
            '--session', dest='session',
            help='Nom ou id de la session dont on veut les participants.')
        parser.add_argument(
            '--examen', dest='examen',
            help="Id de l'examen dont on veut les participants.")

    def handle(self, *args, **options):
        session_arg = options.get('session')
        examen_arg = options.get('examen')
        if not session_arg and not examen_arg:
            raise CommandError(
                'Indiquez --session <id|nom> ou --examen <id>.')

        if examen_arg:
            examens = [get_object_or_404(Examen, pk=examen_arg)]
        else:
            if str(session_arg).isdigit():
                session = get_object_or_404(Session, pk=int(session_arg))
            else:
                session = Session.objects.filter(nom__icontains=str(session_arg)).first()
                if not session:
                    raise CommandError(f'Aucune session trouvée pour « {session_arg} ».')
            self.stdout.write(self.style.NOTICE(
                f'▼ Session : {session.nom} '
                f'(Sem {session.semestre} — {session.get_type_session_display()})'))
            examens = (Examen.objects
                       .filter(session=session)
                       .select_related('cours__promotion', 'cours__enseignant')
                       .order_by('date_examen'))

        if not examens:
            raise CommandError('Aucun examen dans cette session.')

        for examen in examens:
            inscriptions = (examen.inscriptions
                            .select_related('etudiant')
                            .order_by('etudiant__nom', 'etudiant__prenom'))
            self.stdout.write(
                f'\n● {examen.cours.nom} — {examen.cours.promotion.nom} '
                f'({examen.date_examen:%d/%m/%Y %H:%M})')
            if not inscriptions:
                self.stdout.write('   Aucun participant inscrit.')
                continue
            for i, ins in enumerate(inscriptions, 1):
                etu = ins.etudiant
                self.stdout.write(
                    f'   {i:>2}. {etu.numero_etudiant}  '
                    f'{etu.nom.upper()} {etu.prenom}')