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
    )

    print('Count:', meilleures.count())
    print('--- SQL ---')
    print(meilleures.query if hasattr(meilleures, 'query') else 'n/a')
    print('--- actual SQL (debug) ---')
    print(meilleures.query.__str__())


if __name__ == '__main__':
    main()
