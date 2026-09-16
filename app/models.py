import random
import string
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator


def generate_numero_etudiant():
    """Génère un numéro d'étudiant aléatoire et unique (ex: ETU-84920153).

    Appelée uniquement par `Etudiant.save()` quand le numéro est vide : ce
    n'est *pas* un `default=` de champ, car Django évalue les `default`
    appelables à chaque instanciation du modèle — un import de N étudiants
    aurait alors déclenché N SELECT inutiles, même quand le numéro est fourni
    par le fichier Excel.
    """
    chars = string.digits
    while True:
        code = f"ETU-{''.join(random.choices(chars, k=8))}"
        if not Etudiant.objects.filter(numero_etudiant=code).exists():
            return code


class Promotion(models.Model):
    # `unique=True` : les imports Excel s'appuient sur
    # `get_or_create(nom=...)`, qui doit être sûr (une même promotion ne peut
    # pas être créée deux fois). L'index unique généré remplace l'ancien
    # `Index(fields=['nom'])`, devenu redondant.
    nom = models.CharField(max_length=100, unique=True)

    def __str__(self):
        return self.nom

    class Meta:
        ordering = ['nom']


class Enseignant(models.Model):
    noms = models.CharField(max_length=200, default='')
    email = models.EmailField(unique=True)

    def __str__(self):
        return self.noms

    class Meta:
        ordering = ['noms']
        indexes = [models.Index(fields=['noms'])]


class Etudiant(models.Model):
    noms = models.CharField(max_length=150, default='')

    email = models.EmailField(blank=True, null=True)
    numero_etudiant = models.CharField(
        max_length=20, unique=True, blank=True)
    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name='etudiants')

    def save(self, *args, **kwargs):
        if not self.numero_etudiant:
            self.numero_etudiant = generate_numero_etudiant()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.noms

    class Meta:
        ordering = ['noms']
        indexes = [
            models.Index(fields=['noms']),
            models.Index(fields=['promotion', 'noms']),
        ]


class Cours(models.Model):
    nom = models.CharField(max_length=100)
    coefficient = models.IntegerField(default=1)
    enseignant = models.ForeignKey(Enseignant, on_delete=models.SET_NULL, null=True, related_name='cours')
    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name='cours')

    def __str__(self):
        return self.nom

    class Meta:
        ordering = ['nom']
        # Contrainte d'unicité *et* index de recherche : (promotion, nom)
        # sert à la fois `filter(promotion=...)` (trié par nom) et
        # `filter(nom=..., promotion=...)` utilisé par l'import Excel. Les
        # index `['nom']` et `['promotion', 'nom']` qu'elle remplace étaient
        # donc redondants — et chaque index inutile ralentit les INSERT.
        constraints = [
            models.UniqueConstraint(
                fields=['promotion', 'nom'],
                name='unique_cours_promotion_nom',
                violation_error_message=(
                    'Ce cours existe déjà pour cette promotion.'
                ),
            ),
        ]

class Session(models.Model):
    SEMESTRE_CHOICES = [
        (1, 'Semestre 1'),
        (2, 'Semestre 2'),
    ]
    TYPE_CHOICES = [
        ('normale', 'Session Normale'),
        ('rattrapage', 'Rattrapage'),
    ]
    nom = models.CharField(max_length=100)
    semestre = models.IntegerField(choices=SEMESTRE_CHOICES)
    type_session = models.CharField(max_length=20, choices=TYPE_CHOICES)
    date_debut = models.DateField()
    date_fin = models.DateField()
    est_active = models.BooleanField(
        default=False, verbose_name='Session en cours',
        help_text="Désigne la session d'examens actuellement en cours. "
                  "Une seule session peut être active à la fois.")

    def __str__(self):
        return f"{self.nom} - Sem {self.semestre} ({self.get_type_session_display()})"

    def save(self, *args, **kwargs):
        """Garantit l'unicité : si cette session devient active, toutes les
        autres sessions actives sont désactivées (une seule à la fois)."""
        if self.est_active:
            Session.objects.filter(est_active=True).exclude(pk=self.pk)\
                .update(est_active=False)
        super().save(*args, **kwargs)

    class Meta:
        ordering = ['date_debut', 'nom']

class Examen(models.Model):
    cours = models.ForeignKey(Cours, on_delete=models.CASCADE, related_name='examens')
    session = models.ForeignKey(Session, on_delete=models.CASCADE, related_name='examens')
    date_examen = models.DateTimeField()
    salle = models.CharField(max_length=50, blank=True, null=True)
    est_note = models.BooleanField(
        default=False, verbose_name='Noté',
        help_text="Indique si les copies de cet examen ont été corrigées "
                  "(l'examen est alors signalé comme noté).")

    def __str__(self):
        return f"{self.cours} - {self.session} ({self.date_examen})"

    class Meta:
        ordering = ['date_examen']
        constraints = [
            models.UniqueConstraint(
                fields=['cours', 'session'],
                name='unique_examen_cours_session',
                violation_error_message=(
                    'Un examen existe déjà pour ce cours dans cette session.'
                ),
            ),
        ]
        indexes = [
            models.Index(fields=['date_examen']),
            models.Index(fields=['session', 'date_examen']),
        ]

class Inscription(models.Model):
    """Inscription d'un étudiant à un examen, avec ses notes.

    Barème de la fiche de cote : INTERRO/5 + TP/5 + EXAMEN/10 = MOYENNE/20.
    Le champ « note » (moyenne /20) est recalculé automatiquement à chaque
    enregistrement à partir des trois composantes.
    """
    etudiant = models.ForeignKey(Etudiant, on_delete=models.CASCADE, related_name='inscriptions')
    examen = models.ForeignKey(Examen, on_delete=models.CASCADE, related_name='inscriptions')
    note_interro = models.DecimalField(
        max_digits=3, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text="Note d'interrogation sur 5")
    note_tp = models.DecimalField(
        max_digits=3, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(5)],
        help_text="Note de travaux pratiques sur 5")
    note_examen = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(10)],
        help_text="Note d'examen sur 10")
    note = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True,
                               help_text="Moyenne sur 20 (calculée)")

    class Meta:
        # Volontairement *aucun* `ordering` : trier par une FK jointe
        # (« etudiant__noms ») faisait ajouter par Django une jointure
        # `JOIN app_etudiant` + `ORDER BY` à *toutes* les requêtes Inscription,
        # même un simple `filter(examen_id=...)` ou un `count()`. Les vues qui
        # ont besoin d'un tri l'expriment explicitement (`order_by`).
        constraints = [
            models.UniqueConstraint(
                fields=['etudiant', 'examen'],
                name='unique_inscription_etudiant_examen',
            ),
        ]
        indexes = [models.Index(fields=['examen', 'etudiant'])]

    @property
    def moyenne(self):
        """Somme des composantes renseignées (None si tout est vide)."""
        parts = [self.note_interro, self.note_tp, self.note_examen]
        if all(p is None for p in parts):
            return None
        return sum(p for p in parts if p is not None)

    def save(self, *args, **kwargs):
        self.note = self.moyenne
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.etudiant} -> {self.examen}"

