import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Grille, Filiere, Mention, Promotion, GrilleUE, GrilleEtudiant, Etudiant, GrilleNote

grilles = Grille.objects.filter(promotion__nom="L2 SCF LMD")
for g in grilles:
    print(f"Grille: {g}")
    print(f"  total_credits: {g.total_credits}")
    print(f"  bareme: {g.bareme}")
    # Get all grille ues
    grille_ues = g.ues.all()
    print(f"  Nombre de UE dans grille: {grille_ues.count()}")
    for gu in grille_ues:
        print(f"    UE: {gu.intitule}, crédits: {gu.credits}, semestre: {gu.semestre}")
    # Get all grille etudiants
    grille_etudiants = g.lignes.all()
    print(f"  Nombre d'étudiants dans grille: {grille_etudiants.count()}")
    for ge in grille_etudiants:
        etudiant_name = ge.etudiant.noms if ge.etudiant else ge.noms
        print(f"    Etudiant: {etudiant_name}, decision: {ge.decision}, moyenne: {ge.moyenne}, total_pondere: {ge.total_pondere}")
        # Get grille notes for this student and this grille
        grille_notes = ge.notes.all()
        print(f"      Nombre de notes: {grille_notes.count()}")
        for gn in grille_notes:
            print(f"        UE: {gn.ue.intitule}, note: {gn.note}")