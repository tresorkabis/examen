import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Promotion, Grille, Etudiant

p = Promotion.objects.get(pk=71)
g = Grille.objects.get(pk=9)

print("Promotion:", p)
print("Grille:", g)
print("Annee:", g.annee_academique)
print("total_credits:", g.total_credits)
print("Étudiants dans la grille:", g.lignes.count())

# Check if BASAFULA is in this grille
etudiant = Etudiant.objects.get(noms="BASAFULA KUTABIKISA ARLETTE")
ge = g.lignes.filter(etudiant=etudiant).first()
if ge:
    print(f"\nBASAFULA trouvé dans la grille ! Rang: {ge.rang}, Decision: {ge.decision}")
    print(f"Moyenne: {ge.moyenne}, Total pondéré: {ge.total_pondere}")
    # Afficher les notes
    for gn in ge.notes.all()[:5]:
        print(f"  UE: {gn.ue.intitule}, Note: {gn.note}")
else:
    print(f"\nBASAFULA NON trouvé dans cette grille")

# Lister tous les étudiants
print("\nTous les étudiants de la grille :")
for ge in g.lignes.all()[:5]:
    etu = ge.etudiant.noms if ge.etudiant else ge.noms
    print(f"  {ge.rang}. {etu} - Decision: {ge.decision}, Moyenne: {ge.moyenne}")