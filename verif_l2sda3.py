import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from app.models import Promotion, Cours, Grille, GrilleUE

promo = Promotion.objects.get(pk=69)
print('Promotion : %s (pk=%d)' % (promo.nom, promo.pk))
print()

grids = list(Grille.objects.filter(promotion=promo).order_by('-pk'))
print('Grilles de la promotion :')
for g in grids:
    sess = 'session=%d' % g.session_id if g.session_id else 'sans session'
    print('  pk=%d | annee=%s | %s | %d credits' % (g.pk, g.annee_academique, sess, g.total_credits))
print()

if grids:
    grille = grids[0]
    ues = list(grille.ues.select_related('cours').order_by('ordre', 'intitule'))
    print('UE de la grille pk=%d (%s) - %d UE :' % (grille.pk, grille.annee_academique, len(ues)))
    print()
    for ue in ues:
        cours = ue.cours
        if cours:
            cours_str = '%s (pk=%d)' % (cours.nom, cours.pk)
            coeff = str(cours.coefficient)
            statut = 'OK'
        else:
            cours_str = '(aucun cours lie)'
            coeff = ''
            statut = 'NON LIERED'
        num = str(ue.ordre).rjust(2)
        line = '  %s | %-42s | %-3d | S%-2d | %-46s | %-5s | %s' % (
            num, ue.intitule[:42], ue.credits, ue.semestre, cours_str, coeff, statut)
        print(line)
    print()

    linked_ids = set(GrilleUE.objects.filter(
        grille__promotion=promo, cours__isnull=False).values_list('cours_id', flat=True))
    cours_events = Cours.objects.filter(promotion=promo).exclude(pk__in=linked_ids)
    if cours_events:
        print('Cours sans UE liee (%d) :' % len(cours_events))
        for c in cours_events:
            print('  - %s (pk=%d, coef=%s)' % (c.nom, c.pk, c.coefficient))
    else:
        print('Tous les cours ont une UE liee.')
    print()

    total_ue = grille.ues.count()
    liees = GrilleUE.objects.filter(grille=grille, cours__isnull=False).count()
    orphelines = GrilleUE.objects.filter(grille=grille, cours__isnull=True).count()
    print('Recapitulatif : %d UE dans la grille, %d liees a un cours, %d orphelines' % (total_ue, liees, orphelines))
    print('Cours totaux : %d' % promo.nb_cours)