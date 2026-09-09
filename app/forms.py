from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import Promotion, Enseignant, Etudiant, Cours, Session, Examen


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