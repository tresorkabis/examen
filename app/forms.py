from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import (Promotion, Enseignant, Etudiant, Cours, Session, Examen,
                     CotationProjetTutore)


class LoginForm(AuthenticationForm):
    """Formulaire de connexion avec champs stylés Bootstrap."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-control')


class BootstrapModelForm(forms.ModelForm):
    """Applique automatiquement les classes Bootstrap aux champs du formulaire."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.Select, forms.SelectMultiple,
                                          forms.CheckboxSelectMultiple, forms.RadioSelect)):
                css = 'form-select'
            elif isinstance(field.widget, forms.CheckboxInput):
                css = 'form-check-input'
            else:
                css = 'form-control'
            field.widget.attrs.setdefault('class', css)


class PromotionForm(BootstrapModelForm):
    class Meta:
        model = Promotion
        fields = '__all__'


class EnseignantForm(BootstrapModelForm):
    class Meta:
        model = Enseignant
        fields = '__all__'


class EtudiantForm(BootstrapModelForm):
    class Meta:
        model = Etudiant
        fields = '__all__'


class CoursForm(BootstrapModelForm):
    class Meta:
        model = Cours
        fields = '__all__'


class SessionForm(BootstrapModelForm):
    class Meta:
        model = Session
        fields = '__all__'
        widgets = {
            'date_debut': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
            'date_fin': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }


class ExamenForm(BootstrapModelForm):
    class Meta:
        model = Examen
        fields = ['cours', 'session', 'date_examen', 'salle']
        widgets = {
            'date_examen': forms.DateTimeInput(
                attrs={'type': 'datetime-local'},
                format='%Y-%m-%dT%H:%M'),
        }

    def __init__(self, *args, **kwargs):
        instance = kwargs.get('instance')
        super().__init__(*args, **kwargs)
        self.fields['cours'].queryset = (Cours.objects
                                         .select_related('promotion', 'enseignant')
                                         .order_by('promotion__nom', 'nom'))
        # Libellé enrichi pour la recherche : cours, promotion, enseignant.
        def label_cours(c):
            parts = [f"{c.nom} ({c.promotion.nom})"]
            if c.enseignant:
                parts.append(f"— {c.enseignant}")
            return ' '.join(parts)
        self.fields['cours'].label_from_instance = label_cours
        self.fields['date_examen'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['salle'].initial = 'Local 1'
        # À la création, présélectionner la session en cours (si définie).
        if instance is None:
            active = Session.objects.filter(est_active=True).first()
            if active:
                self.fields['session'].initial = active.pk


class CotationProjetTutoreForm(BootstrapModelForm):
    """Fiche de cotation du projet tutoré (défense L3), barème /200.

    Les 18 champs de notes sont en `localize=True` : la saisie accepte la
    virgule décimale française (« 14,5 ») autant que le point, et la valeur
    réaffichée dans le champ reprend le séparateur local. Les `help_text`
    du modèle (« sur 10 », « sur 5 »…) portent le barème de chaque case.
    """

    class Meta:
        model = CotationProjetTutore
        fields = ['section', 'sujet', 'domaine_sujet', 'nom_lecteur',
                  'qualite_lecteur', 'date_cotation',
                  'directeur', 'lecteur1', 'lecteur2'] + [
            CotationProjetTutore.champ_de(rubrique, membre)
            for rubrique, *_ in CotationProjetTutore.RUBRIQUES
            for membre, _ in CotationProjetTutore.MEMBRES
        ]
        widgets = {
            'date_cotation': forms.DateInput(
                attrs={'type': 'date'}, format='%Y-%m-%d'),
        }
        labels = {
            'directeur': 'Directeur (jury)',
            'lecteur1': '1er Lecteur (jury)',
            'lecteur2': '2ème Lecteur (jury)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Jury : listes déroulantes triées, avec une option vide explicite.
        for champ in ('directeur', 'lecteur1', 'lecteur2'):
            self.fields[champ].queryset = (
                Enseignant.objects.order_by('noms'))
            self.fields[champ].empty_label = '— Non affecté —'
        # Notes saisies en français : « 14,5 » (virgule) comme « 14.5 ».
        # Chaque champ porte aussi `data-membre` (colonne du jury) pour le
        # calcul live des totaux en bas de tableau, et le barème en `max`.
        for rubrique, _numero, _groupe, _libelle, bareme in (
                CotationProjetTutore.RUBRIQUES):
            for membre, _libelle_membre in CotationProjetTutore.MEMBRES:
                champ = self.fields[CotationProjetTutore.champ_de(
                    rubrique, membre)]
                champ.localize = True
                champ.widget.attrs.update({
                    'class': 'form-control text-center',
                    'data-membre': membre,
                    'max': bareme[membre],
                    'inputmode': 'decimal',
                })

    def clean(self):
        """Refuse qu'un même enseignant cumule deux rôles dans le jury."""
        nettoyees = super().clean()
        roles = [('directeur', nettoyees.get('directeur')),
                 ('1er lecteur', nettoyees.get('lecteur1')),
                 ('2ème lecteur', nettoyees.get('lecteur2'))]
        vus = {}
        for libelle, enseignant in roles:
            if enseignant is None:
                continue
            if enseignant.pk in vus:
                self.add_error(
                    None,
                    f'{enseignant} ne peut pas cumuler deux rôles dans le '
                    f'jury ({vus[enseignant.pk]} et {libelle}).')
                break
            vus[enseignant.pk] = libelle
        return nettoyees


class ExcelImportForm(forms.Form):
    TYPE_CHOICES = [
        ('etudiants', 'Étudiants'),
        ('enseignants', 'Enseignants'),
        ('cours', 'Cours'),
        ('grilles', 'Grille de délibération'),
    ]

    type_import = forms.ChoiceField(
        choices=TYPE_CHOICES,
        label="Type de données à importer",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    fichier_excel = forms.FileField(
        label="Fichier Excel (.xlsx, .xls)",
        widget=forms.FileInput(attrs={'class': 'form-control', 'accept': '.xlsx, .xls'})
    )
    promotion = forms.ModelChoiceField(
        queryset=Promotion.objects.all(),
        required=False,
        label="Promotion par défaut (optionnel)",
        help_text="Attribuée si la promotion n'est pas spécifiée dans le fichier.",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    # Une grille est annuelle alors qu'une session est ponctuelle : le lien
    # reste facultatif, mais c'est lui qui fait apparaître la grille dans le
    # détail de la session. Pour une grille d'une année antérieure (ex. L1
    # 2024-2025 des actuels L2), on le laisse vide : la grille vit sur la
    # fiche de la promotion et l'historique des étudiants, pas dans une
    # session d'examens.
    session = forms.ModelChoiceField(
        queryset=Session.objects.all(),
        required=False,
        label="Session d'examens (grilles — optionnel)",
        help_text="Vide sauf si la grille doit apparaître dans l'onglet « Grilles » d'une session.",
        widget=forms.Select(attrs={'class': 'form-select'})
    )
    # Année académique : elle est normalement lue dans le titre du fichier
    # (« GRILLE 2025 - 2026 »), mais un fichier d'une année antérieure
    # (ex. grille L1 2024-2025 des actuels L2) peut ne pas la porter — ou la
    # porter de façon ambiguë. La valeur saisie ici prime alors sur la
    # détection automatique.
    annee_academique = forms.CharField(
        max_length=20,
        required=False,
        label="Année académique (grilles)",
        help_text="Ex. « 2024-2025 ». Vide : l'année est lue dans le titre du fichier.",
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '2024-2025',
        })
    )

    def clean_fichier_excel(self):
        f = self.cleaned_data.get('fichier_excel')
        if f:
            ext = f.name.split('.')[-1].lower()
            if ext not in ['xlsx', 'xls']:
                raise forms.ValidationError("Le fichier doit être au format Excel (.xlsx ou .xls).")
        return f

    def clean(self):
        """Une grille est *par promotion* : elle ne peut pas s'en passer.

        Les autres imports s'en passent (la promotion est lue dans le fichier
        ou déduite du nom de la feuille).
        """
        donnees = super().clean()
        if (donnees.get('type_import') == 'grilles'
                and not donnees.get('promotion')):
            self.add_error(
                'promotion',
                "Pour une grille de délibération, la promotion est obligatoire "
                "(une grille couvre une seule promotion).")
        return donnees