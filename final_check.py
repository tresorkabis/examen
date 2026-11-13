import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Etudiant, Inscription, Grille, GrilleEtudiant

etudiant = Etudiant.objects.get(noms="BASAFULA KUTABIKISA ARLETTE")
print(f"Étudiant: {etudiant.noms}")
print(f"Matricule: {etudiant.numero_etudiant}")
print(f"Promotion: {etudiant.promotion}")
print(f"Date inscription: {etudiant.date_naissance}")

# Vérifier inscriptions
inscriptions = Inscription.objects.filter(etudiant=etudiant)
print(f"\nInscriptions: {inscriptions.count()}")
for i in inscriptions:
    print(f"  - {i.examen.session}: note={i.note}")

# Vérifier grilles
print(f"\nRecherche dans les grilles...")
toutes_grilles = Grille.objects.all()
for g in toutes_grilles:
    ge = g.lignes.filter(etudiant=etudiant).first()
    if ge:
        print(f"  Grille {g.promotion} {g.annee_academique}: trouvé ! Rang {ge.rang}, Decision: {ge.decision}")
        # Afficher les notes
        for gn in ge.notes.all()[:3]:  # Afficher les 3 premières
            print(f"    UE: {gn.ue.intitule}, Note: {gn.note}")