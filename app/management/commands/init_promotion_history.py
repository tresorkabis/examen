from django.core.management.base import BaseCommand
from app.models import Etudiant, HistoriquePromotion
from django.utils import timezone

class Command(BaseCommand):
    help = 'Initialise l\'historique des promotions pour tous les étudiants existants'

    def handle(self, *args, **options):
        etudiants = Etudiant.objects.all()
        count = 0
        
        for etudiant in etudiants:
            # On vérifie s'il n'y a pas déjà d'historique pour éviter les doublons
            if not HistoriquePromotion.objects.filter(etudiant=etudiant).exists():
                HistoriquePromotion.objects.create(
                    etudiant=etudiant,
                    promotion=etudiant.promotion,
                    date_debut=timezone.now().date()
                )
                count += 1
        
        self.stdout.write(self.style.SUCCESS(f'Historique initialisé pour {count} étudiants.'))
