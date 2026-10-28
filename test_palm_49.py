def main():
    import os

    import django

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()

    from django.db.models import Max, Min

    from app.models import Etudiant, Grille, GrilleEtudiant

    grille = Grille.objects.get(pk=49)
    print('Grille 49 historique_promotions count:', grille.parcours.count())

    meilleures = (
        GrilleEtudiant.objects
        .filter(grille__parcours__isnull=False, etudiant__isnull=False)
        .filter(grille=grille)
        .values('etudiant')
        .annotate(meilleur_rang=Min('rang'), meilleure_moyenne=Max('moyenne'))
        .order_by('meilleur_rang', '-meilleure_moyenne')
    )
    print('Palmares count for grille 49:', meilleures.count())
    for resultat in meilleures:
        etudiant = Etudiant.objects.get(pk=resultat['etudiant'])
        print(
            '  etudiant_id', resultat['etudiant'], '|', etudiant.noms,
            '| rang', resultat['meilleur_rang'], '| moy',
            resultat['meilleure_moyenne'],
        )

    print()
    print('Rows excluded (etudiant_id=NULL):')
    exclus = GrilleEtudiant.objects.filter(
        grille=grille, etudiant_id__isnull=True
    ).order_by('rang')
    for ligne in exclus:
        print(
            '  rang', ligne.rang, '| noms:', repr(ligne.noms),
            '| moyenne:', ligne.moyenne,
        )


if __name__ == '__main__':
    main()
