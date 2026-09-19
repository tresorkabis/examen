import random
import string
from decimal import Decimal

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


class Grille(models.Model):
    """Grille de délibération d'une promotion (un fichier Excel archivé).

    Les grilles (data/L*.xlsx) récapitulent, pour une promotion : les UE des
    deux semestres avec leurs crédits, les notes /20 de chaque étudiant et la
    synthèse de délibération (crédits validés, total pondéré, moyenne,
    décision, mention).

    Ce modèle ne recalcule rien : il recopie la grille, qui est une pièce
    délibérée. `total_credits` (somme des crédits des UE) est le dénominateur
    de la moyenne du fichier — `moyenne = total_pondéré / total_credits`, la
    cellule « total crédits × 20 » n'étant que l'écriture ×20 du même rapport.

    Structure : `Grille` (en-tête) -> `GrilleUE` (colonnes) et
    `GrilleEtudiant` (lignes) -> `GrilleNote` (cellules).
    """

    promotion = models.ForeignKey(
        Promotion, on_delete=models.CASCADE, related_name='grilles')
    # Session facultative : une grille est annuelle alors qu'une session
    # d'examens est ponctuelle. Le lien sert à afficher la grille dans le
    # détail d'une session ; `SET_NULL` la conserve si la session disparaît.
    session = models.ForeignKey(
        Session, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='grilles',
        help_text="Session d'examens où la grille est consultée.")
    annee_academique = models.CharField(max_length=20, default='2025-2026')
    etablissement = models.CharField(
        max_length=100, blank=True, default='', verbose_name='Établissement')
    intitule = models.CharField(max_length=200, blank=True, default='')
    total_credits = models.PositiveSmallIntegerField(
        default=0,
        help_text="Somme des crédits des UE (dénominateur de la moyenne).")
    fichier_source = models.CharField(
        max_length=255, blank=True, default='',
        help_text="Nom du fichier Excel d'origine (traçabilité).")
    importe_le = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.promotion.nom} — {self.annee_academique}'

    @property
    def bareme(self):
        """Total pondéré maximal (crédits × 20) : dénominateur du fichier."""
        return self.total_credits * 20

    class Meta:
        # Tri par date seule : un `ordering` sur une FK jointe ferait ajouter
        # une jointure + ORDER BY à *toutes* les requêtes (cf. Inscription).
        ordering = ['-importe_le']
        constraints = [
            models.UniqueConstraint(
                fields=['promotion', 'session', 'annee_academique'],
                name='unique_grille_promotion_session_annee',
                violation_error_message=(
                    'Une grille existe déjà pour cette promotion dans cette '
                    'session et cette année académique.'
                ),
            ),
        ]
        indexes = [models.Index(fields=['promotion', 'annee_academique'])]


class GrilleUE(models.Model):
    """Colonne « UE » d'une grille : intitulé, crédits, semestre et groupe.

    `cours` relie l'UE au référentiel de l'application (renseigné à l'import
    quand l'intitulé correspond). Il est nullifiable : la grille est une pièce
    archivée, elle doit rester lisible même si le cours est supprimé — et son
    intitulé est de toute façon recopié dans `intitule`.
    """

    SEMESTRE_CHOICES = [
        (1, 'Semestre 1'),
        (2, 'Semestre 2'),
    ]

    grille = models.ForeignKey(
        Grille, on_delete=models.CASCADE, related_name='ues')
    cours = models.ForeignKey(
        Cours, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='grille_ues')
    intitule = models.CharField(max_length=100)
    credits = models.PositiveSmallIntegerField(default=1)
    semestre = models.IntegerField(choices=SEMESTRE_CHOICES)
    groupe = models.CharField(
        max_length=20, blank=True, default='',
        help_text="Code du groupe d'UE du fichier (IBA, LAN, MIF…).")
    ordre = models.PositiveSmallIntegerField(
        default=0, help_text='Position de la colonne dans le fichier.')

    def __str__(self):
        return f'{self.intitule} (S{self.semestre}, {self.credits} cr)'

    class Meta:
        # `ordre` reconstitue l'ordre des colonnes du fichier ; l'intitulé
        # sert de départage si deux UE partagent une position.
        ordering = ['semestre', 'ordre', 'intitule']
        constraints = [
            models.UniqueConstraint(
                fields=['grille', 'intitule'],
                name='unique_grilleue_grille_intitule',
                violation_error_message=(
                    'Cette UE figure déjà deux fois dans la grille.'
                ),
            ),
        ]


class GrilleEtudiant(models.Model):
    """Ligne « étudiant » d'une grille, avec sa synthèse de délibération.

    Les colonnes de synthèse (crédits validés par semestre, total pondéré,
    moyenne, décision, mention) recopient les colonnes calculées du fichier
    (Q à AN) : ce sont les résultats *délibérés*, ils ne doivent pas être
    recalculés à l'affichage. `total_pondere` est même saisi en dur dans le
    fichier, ce qui justifie de le conserver plutôt que de le déduire des
    notes — la comparaison des deux sert de contrôle d'intégrité.
    """

    DECISION_CHOICES = [
        ('V', 'Validé'),
        ('VC', 'Validé avec complément'),
        ('NV', 'Non validé'),
    ]
    MENTION_CHOICES = [
        ('Passable', 'Passable'),
        ('Assez Bien', 'Assez Bien'),
        ('Bien', 'Bien'),
        ('Très Bien', 'Très Bien'),
        ('Excellent', 'Excellent'),
    ]

    grille = models.ForeignKey(
        Grille, on_delete=models.CASCADE, related_name='lignes')
    etudiant = models.ForeignKey(
        Etudiant, on_delete=models.CASCADE, related_name='grille_lignes')
    rang = models.PositiveSmallIntegerField(
        help_text="N° d'ordre de l'étudiant dans la grille.")
    credits_s1 = models.PositiveSmallIntegerField(default=0)
    credits_s2 = models.PositiveSmallIntegerField(default=0)
    credits_total = models.PositiveSmallIntegerField(default=0)
    nb_ue_reprendre = models.PositiveSmallIntegerField(
        default=0, verbose_name='UE à reprendre')
    total_pondere = models.PositiveIntegerField(default=0)
    moyenne = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        help_text='Moyenne /20 délibérée (null si non calculable).')
    pourcentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text='Réussite en pourcentage (0-100).')
    decision = models.CharField(
        max_length=2, choices=DECISION_CHOICES, blank=True, default='')
    mention = models.CharField(
        max_length=20, choices=MENTION_CHOICES, blank=True, default='')

    def __str__(self):
        return f'{self.rang}. {self.etudiant.noms}'

    @property
    def moyenne_affichee(self):
        """Moyenne prête à afficher : sans décimale superflue.

        Même logique que `GrilleNote.note_affichee` : la moyenne
        délibérée (ex. ``7.45``) s'affiche telle quelle, mais ``12.00``
        devient ``12`` — jamais de virgule inutile.
        """
        if self.moyenne is None or self.moyenne == '':
            return ''
        valeur = Decimal(self.moyenne).normalize()
        return format(valeur, 'f')

    class Meta:
        ordering = ['rang']
        constraints = [
            models.UniqueConstraint(
                fields=['grille', 'etudiant'],
                name='unique_grilleetudiant_grille_etudiant',
                violation_error_message=(
                    'Cet étudiant figure déjà dans la grille.'
                ),
            ),
            models.UniqueConstraint(
                fields=['grille', 'rang'],
                name='unique_grilleetudiant_grille_rang',
                violation_error_message=(
                    'Ce numéro d\'ordre est déjà utilisé dans la grille.'
                ),
            ),
        ]


class GrilleNote(models.Model):
    """Note /20 d'un étudiant dans une UE : une cellule de la grille.

    `note` à NULL signifie « case vide », pas « zéro » : le fichier compte ces
    cases comme des échecs (COUNTBLANK), mais la distinction reste nécessaire
    pour afficher — et corriger — ce qui n'a pas été noté.
    """

    SEUIL_VALIDATION = 10

    ligne = models.ForeignKey(
        GrilleEtudiant, on_delete=models.CASCADE, related_name='notes')
    ue = models.ForeignKey(
        GrilleUE, on_delete=models.CASCADE, related_name='notes')
    note = models.DecimalField(
        max_digits=4, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(20)],
        help_text='Note /20 (null = case vide dans la grille).')

    def __str__(self):
        return f'{self.ligne.etudiant.noms} — {self.ue.intitule}'

    @property
    def note_affichee(self):
        """Note prête à afficher : entier sans décimale superflue.

        Le fichier Excel ne contient que des entiers (``12``, pas
        ``12,50``) : afficher ``{{ note.note }}`` forcerait le format
        ``12,00`` (localisation fr). Cette propriété retire les zéros
        inutiles — ``Decimal('12.00')`` -> ``'12'`` — sans jamais
        ajouter de séparateur décimal.
        """
        if self.note is None or self.note == '':
            return ''
        valeur = Decimal(self.note).normalize()
        return format(valeur, 'f')

    @property
    def est_validee(self):
        """L'UE est-elle acquise ? Critère du fichier : note >= 10.

        La valeur est convertie explicitement : après un `create()`/`save()`
        non suivi de `refresh_from_db()`, l'attribut porte encore ce qui a été
        passé à l'ORM (souvent la chaîne lue dans Excel), et non le `Decimal`
        relu depuis la base.
        """
        if self.note is None or self.note == '':
            return False
        return Decimal(self.note) >= self.SEUIL_VALIDATION

    class Meta:
        # Volontairement aucun `ordering` : trier par une FK jointe ajouterait
        # une jointure à *toutes* les requêtes (cf. Inscription).
        constraints = [
            models.UniqueConstraint(
                fields=['ligne', 'ue'],
                name='unique_grillenote_ligne_ue',
                violation_error_message=(
                    'Cette note existe déjà pour cet étudiant et cette UE.'
                ),
            ),
        ]
        indexes = [models.Index(fields=['ue', 'ligne'])]

