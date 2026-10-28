def main():
    import os

    import django

    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    django.setup()

    from app.models import Grille, GrilleEtudiant

    grille = Grille.objects.get(pk=49)
    meilleures = (
        GrilleEtudiant.objects
        .filter(grille__parcours__isnull=False, etudiant__isnull=False)
        .filter(grille=grille)
        .values('etudiant')
    )

    print('Count:', meilleures.count())
    sql = meilleures.query
    print('--- SQL ---')
    print(sql)
    print('--- params ---')
    print(sql.params)


if __name__ == '__main__':
    main()
