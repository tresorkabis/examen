import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from app.models import Etudiant, Inscription, Session, Grille, GrilleEtudiant, GrilleNote

etudiant = Etudiant.objects.get(noms="BASAFULA KUTABIKISA ARLETTE")
print("Étudiant:", etudiant.noms, "| Matricule:", etudiant.numero_etudiant)
print("Promotion:", etudiant.promotion)

inscriptions = Inscription.objects.filter(etudiant=etudiant)
print(f"\nInscriptions ({inscriptions.count()}):")
for i in inscriptions:
    print(f"  Examen: {i.examen.session}")
    print(f"    note_interro: {i.note_interro}, note_tp: {i.note_tp}, note_examen: {i.note_examen}")
    print(f"    note calculée: {i.note}")

print("\n" + "="*60 + "\n")

# Check grilles for this student
sessions = Session.objects.all()
print("Sessions et grilles correspondantes :")
for session in sessions:
    grilles = session.grilles.filter(promotion=etudiant.promotion)
    for grille in grilles:
        print(f"\n  Session: {session} | Grille: {grille} (année: {grille.annee_academique})")
        # Vérifier si l'étudiant est dans cette grille
        ge = grille.lignes.filter(etudiant=etudiant).first()
        if ge:
            print(f"    -> Étudiant trouvé dans la grille !")
            print(f"       Décision: {ge.decision}, Moyenne: {ge.moyenne}, Total pondéré: {ge.total_pondere}")
            # Vérifier les notes de la grille
            notes = grille.notes.all()
            print(f"    -> Notes de la grille ({notes.count()} UE notées) :")
            for gn in notes:
                print(f"       UE: {gn.ue.intitule}, Note: {gn.note}")
        else:
            print(f"    -> Étudiant NON trouvé dans la grille")