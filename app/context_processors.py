"""
Context processors de l'application « app ».

breadcrumbs : injecte un fil d'Ariane dans le contexte de tous les templates,
généré à partir du nom de la vue résolue (request.resolver_match.url_name).
Le premier niveau pointe toujours vers le tableau de bord ; les pages
parents sont cliquables pour remonter dans la navigation.
"""

# url_name de la vue -> (libellé de la page courante, [(libellé parent, url_name), ...])
BREADCRUMB_MAP = {
    # Tableau de bord
    "dashboard": ("Tableau de Bord", []),

    # Listes
    "etudiant_list": ("Étudiants", []),
    "etudiant_detail": ("Détail", [("Étudiants", "etudiant_list")]),
    "cours_list": ("Cours", []),
    "cours_detail": ("Détail", [("Cours", "cours_list")]),
    "promotion_list": ("Promotions", []),
    "enseignant_list": ("Enseignants", []),
    "session_list": ("Sessions", []),
    "session_detail": ("Détail", [("Sessions", "session_list")]),
    "examen_list": ("Examens", []),

    # Créations
    "etudiant_create": ("Nouvel Étudiant", [("Étudiants", "etudiant_list")]),
    "cours_create": ("Nouveau Cours", [("Cours", "cours_list")]),
    "promotion_create": ("Nouvelle Promotion", [("Promotions", "promotion_list")]),
    "promotion_detail": ("Détail", [("Promotions", "promotion_list")]),
    "enseignant_detail": ("Détail", [("Enseignants", "enseignant_list")]),
    "enseignant_create": ("Nouvel Enseignant", [("Enseignants", "enseignant_list")]),
    "session_create": ("Nouvelle Session", [("Sessions", "session_list")]),
    "examen_create": ("Nouvel Examen", [("Examens", "examen_list")]),

    # Modifications
    "etudiant_update": ("Modifier un Étudiant", [("Étudiants", "etudiant_list")]),
    "cours_update": ("Modifier un Cours", [("Cours", "cours_list")]),
    "promotion_update": ("Modifier une Promotion", [("Promotions", "promotion_list")]),
    "enseignant_update": ("Modifier un Enseignant", [("Enseignants", "enseignant_list")]),
    "session_update": ("Modifier une Session", [("Sessions", "session_list")]),
    "examen_update": ("Modifier un Examen", [("Examens", "examen_list")]),

    # Suppressions
    "etudiant_delete": ("Supprimer un Étudiant", [("Étudiants", "etudiant_list")]),
    "cours_delete": ("Supprimer un Cours", [("Cours", "cours_list")]),
    "promotion_delete": ("Supprimer une Promotion", [("Promotions", "promotion_list")]),
    "enseignant_delete": ("Supprimer un Enseignant", [("Enseignants", "enseignant_list")]),
    "session_delete": ("Supprimer une Session", [("Sessions", "session_list")]),
    "examen_delete": ("Supprimer un Examen", [("Examens", "examen_list")]),
}


def breadcrumbs(request):
    """Ajoute « breadcrumb » et « current_url_name » au contexte.

    current_url_name : nom de la vue résolue (ex. « etudiant_list »), exposé
    dans tous les templates pour l'état actif de la navigation.
    """
    resolver_match = getattr(request, "resolver_match", None)
    url_name = getattr(resolver_match, "url_name", None)
    entry = BREADCRUMB_MAP.get(url_name)
    if entry is None:
        # Hors de l'application (ex. /admin/) : aucun fil d'Ariane.
        return {"current_url_name": url_name}
    current, parents = entry
    return {
        "breadcrumb": {
            "current": current,
            "parents": parents,
        },
        "current_url_name": url_name,
    }