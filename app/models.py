from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator

class Promotion(models.Model):
    nom = models.CharField(max_length=100)

    def __str__(self):
        return self.nom

    class Meta:
        ordering = ['nom']
        indexes = [models.Index(fields=['nom'])]


class Enseignant(models.Model):
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    email = models.EmailField(unique=True)

    def __str__(self):
        return f"{self.nom} {self.prenom}"

    class Meta:
        ordering = ['nom', 'prenom']
        indexes = [models.Index(fields=['nom', 'prenom'])]


class Etudiant(models.Model):
    nom = models.CharField(max_length=100)
    prenom = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    numero_etudiant = models.CharField(max_length=20, unique=True)
    promotion = models.ForeignKey(Promotion, on_delete=models.CASCADE, related_name='etudiants')

    def __str__(self):
        return f"{self.nom} {self.prenom}"

    class Meta:
        ordering = ['nom', 'prenom']
        indexes = [
            models.Index(fields=['nom', 'prenom']),
            models.Index(fields=['promotion', 'nom', 'prenom']),
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
        indexes = [
            models.Index(fields=['nom']),
            models.Index(fields=['promotion', 'nom']),
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

    def __str__(self):
        return f"{self.nom} - Sem {self.semestre} ({self.get_type_session_display()})"

    class Meta:
        ordering = ['date_debut', 'nom']

class Examen(models.Model):
    cours = models.ForeignKey(Cours, on_delete=models.CASCADE)
    session = models.ForeignKey(Session, on_delete=models.CASCADE)
    date_examen = models.DateTimeField()
    salle = models.CharField(max_length=50, blank=True, null=True)

    def __str__(self):
        return f"{self.cours} - {self.session} ({self.date_examen})"

    class Meta:
        ordering = ['date_examen']
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
    etudiant = models.ForeignKey(Etudiant, on_delete=models.CASCADE)
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
        unique_together = ('etudiant', 'examen')
        ordering = ['etudiant']
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

