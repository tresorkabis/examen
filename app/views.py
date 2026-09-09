from collections import OrderedDict
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.messages.views import SuccessMessageMixin
from django.core.exceptions import ValidationError
from django.core.cache import cache
from django.db.models import Count, Prefetch, Q
from django.http import FileResponse, Http404, HttpResponseRedirect
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.text import slugify
from django.views.generic import (ListView, CreateView, UpdateView, DeleteView,
                                  DetailView)

from .forms import (PromotionForm, EnseignantForm, EtudiantForm, CoursForm,
                    SessionForm, ExamenForm)
from .models import Etudiant, Cours, Session, Enseignant, Promotion, Examen, Inscription


def _compteurs_tableau_de_bord():
    """Compteurs du dashboard, mis en cache 60 s (4 COUNT → 0 requête)."""
    compteurs = cache.get('dashboard_compteurs')
    if compteurs is None:
        compteurs = {
            'total_etudiants': Etudiant.objects.count(),
            'total_cours': Cours.objects.count(),
            'total_sessions': Session.objects.count(),
            'total_enseignants': Enseignant.objects.count(),
        }
        cache.set('dashboard_compteurs', compteurs, 60)
    return compteurs


def dashboard(request):
    compteurs = _compteurs_tableau_de_bord()
    prochains = (Examen.objects
                 .select_related('cours__promotion', 'cours__enseignant', 'session')
                 .filter(date_examen__gte=timezone.now())
                 .order_by('date_examen')[:5])
    dernieres_sessions = (Session.objects.order_by('-date_debut')[:3])
    context = {
        **compteurs,
        'prochains_examens': prochains,
        'dernieres_sessions': dernieres_sessions,
        'total_promotions': Promotion.objects.count(),
        'total_examens': Examen.objects.count(),
    }
    return render(request, 'app/dashboard.html', context)


class SafePaginationMixin:
    """Si « ?page=N » est hors bornes, redirige vers la dernière page valide.

    Django lève normalement une 404 pour une page inexistante (ex. « ?page=8 »
    alors qu'il n'y a que 5 pages — lien périmé, favori, ancienne pagination).
    Ce mixin transforme cette 404 en redirection vers la dernière page,
    en conservant les éventuels autres paramètres GET de la requête.
    """

    def get(self, request, *args, **kwargs):
        try:
            return super().get(request, *args, **kwargs)
        except Http404:
            page_arg = request.GET.get(self.page_kwarg)
            queryset = self.get_queryset()
            page_size = self.get_paginate_by(queryset)
            # Ce n'est pas une 404 de pagination : on la propage telle quelle.
            if page_arg is None or not page_size:
                raise
            paginator = self.get_paginator(queryset, page_size)
            if paginator.num_pages < 1:
                raise
            try:
                requested = int(page_arg)
            except ValueError:
                requested = 1
            target = min(max(requested, 1), paginator.num_pages)
            query = request.GET.copy()
            query[self.page_kwarg] = target
            return HttpResponseRedirect(f"{request.path}?{query.urlencode()}")


    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Conserve les paramètres GET (filtres…) dans les liens de pagination
        params = self.request.GET.copy()
        params.pop(self.page_kwarg, None)
        context['qs'] = f"&{params.urlencode()}" if params else ""
        return context


class DeleteMessageMixin:
    """Affiche un message de confirmation après une suppression.

    En Django 6, DeleteView passe par FormMixin : le POST appelle form_valid()
    (et non delete()), c'est donc là qu'il faut accrocher le message.
    """
    success_message = None

    def form_valid(self, form):
        response = super().form_valid(form)
        if self.success_message:
            messages.success(self.request, self.success_message % {"object": self.object})
        return response


# --- CRUD ETUDIANT ---
class EtudiantListView(ListView):
    """Liste des étudiants regroupés par promotion, sous forme de cartes.

    Pas de pagination : chaque promotion forme une carte (accordéon).
    Une recherche « ?q= » filtre les étudiants par nom / prénom / numéro /
    email et n'affiche que les promotions concernées (cartes ouvertes).
    """
    model = Promotion
    template_name = "app/etudiant_list.html"
    context_object_name = "promotions"

    def get_queryset(self):
        q = self.request.GET.get('q', '').strip()
        if q:
            # Recherche en base (indexée) : ne charge que les promotions
            # ayant au moins un étudiant correspondant.
            return (Promotion.objects
                    .filter(Q(etudiants__nom__icontains=q)
                            | Q(etudiants__prenom__icontains=q)
                            | Q(etudiants__numero_etudiant__icontains=q)
                            | Q(etudiants__email__icontains=q))
                    .distinct()
                    .prefetch_related(Prefetch(
                        'etudiants',
                        queryset=Etudiant.objects.filter(
                            Q(nom__icontains=q)
                            | Q(prenom__icontains=q)
                            | Q(numero_etudiant__icontains=q)
                            | Q(email__icontains=q))
                        .order_by('nom', 'prenom')))
                    .order_by('nom'))
        if self.request.GET.get('toutes') == '1':
            return (Promotion.objects
                    .prefetch_related(Prefetch(
                        'etudiants',
                        queryset=Etudiant.objects.order_by('nom', 'prenom')))
                    .order_by('nom'))
        # Par défaut : seules les promotions non vides (EXISTS, sans tout charger).
        return (Promotion.objects
                .filter(etudiants__isnull=False)
                .distinct()
                .prefetch_related(Prefetch(
                    'etudiants',
                    queryset=Etudiant.objects.order_by('nom', 'prenom')))
                .order_by('nom'))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        q = self.request.GET.get('q', '').strip()
        groups = list(context['promotions'])
        for promotion in groups:
            # Déjà filtré/trié en base via le Prefetch du get_queryset.
            promotion.filtered_etudiants = list(promotion.etudiants.all())
        context['groups'] = groups
        context['q'] = q
        context['open_all'] = bool(q)
        return context


class EtudiantCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Etudiant
    form_class = EtudiantForm
    template_name = "app/etudiant_form.html"
    success_url = reverse_lazy("etudiant_list")
    success_message = "L'étudiant « %(prenom)s %(nom)s » a été créé avec succès."

    def get_initial(self):
        """Pré-sélectionne la promotion via « ?promotion=<pk> » (bouton + d'une carte)."""
        initial = super().get_initial()
        promo = self.request.GET.get('promotion')
        if promo and promo.isdigit() and Promotion.objects.filter(pk=promo).exists():
            initial['promotion'] = int(promo)
        return initial


class EtudiantUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Etudiant
    form_class = EtudiantForm
    template_name = "app/etudiant_form.html"
    success_url = reverse_lazy("etudiant_list")
    success_message = "L'étudiant « %(prenom)s %(nom)s » a été modifié."


class EtudiantDeleteView(LoginRequiredMixin, DeleteMessageMixin, DeleteView):
    model = Etudiant
    template_name = "app/etudiant_confirm_delete.html"
    success_url = reverse_lazy("etudiant_list")
    success_message = "L'étudiant « %(object)s » a été supprimé."


# --- CRUD PROMOTION ---
class PromotionListView(SafePaginationMixin, ListView):
    model = Promotion
    template_name = "app/promotion_list.html"
    context_object_name = "promotions"
    paginate_by = 25

    def get_queryset(self):
        return (Promotion.objects
                .annotate(
                    nb_etudiants=Count('etudiants', distinct=True),
                    nb_cours=Count('cours', distinct=True),
                    nb_enseignants=Count('cours__enseignant', distinct=True),
                )
                .order_by('nom'))


class PromotionDetailView(DetailView):
    """Fiche détaillée d'une promotion : effectif, cours et enseignants."""
    model = Promotion
    template_name = "app/promotion_detail.html"
    context_object_name = "promotion"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        promotion = self.object
        cours = (promotion.cours
                 .select_related('enseignant')
                 .order_by('nom'))
        etudiants = (promotion.etudiants
                     .order_by('nom', 'prenom'))
        # Enseignants intervenant dans cette promotion : requête ORM triée,
        # avec le nombre de cours dispensés par chacun.
        enseignants = (Enseignant.objects
                       .filter(cours__promotion=promotion)
                       .annotate(nb_cours=Count('cours',
                                                filter=Q(cours__promotion=promotion),
                                                distinct=True))
                       .order_by('nom', 'prenom'))
        nb_noted = Inscription.objects.filter(
            etudiant__promotion=promotion).count()
        ctx.update({
            'cours': cours,
            'etudiants': etudiants,
            'enseignants': enseignants,
            'nb_inscriptions': nb_noted,
        })
        return ctx


class PromotionCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Promotion
    form_class = PromotionForm
    template_name = "app/promotion_form.html"
    success_url = reverse_lazy("promotion_list")
    success_message = "La promotion « %(nom)s » a été créée."


class PromotionUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Promotion
    form_class = PromotionForm
    template_name = "app/promotion_form.html"
    success_url = reverse_lazy("promotion_list")
    success_message = "La promotion « %(nom)s » a été modifiée."


class PromotionDeleteView(LoginRequiredMixin, DeleteMessageMixin, DeleteView):
    model = Promotion
    template_name = "app/promotion_confirm_delete.html"
    success_url = reverse_lazy("promotion_list")
    success_message = "La promotion « %(object)s » a été supprimée."


# --- CRUD ENSEIGNANT ---
class EnseignantListView(SafePaginationMixin, ListView):
    model = Enseignant
    template_name = "app/enseignant_list.html"
    context_object_name = "enseignants"
    paginate_by = 25


class EnseignantCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Enseignant
    form_class = EnseignantForm
    template_name = "app/enseignant_form.html"
    success_url = reverse_lazy("enseignant_list")
    success_message = "L'enseignant « %(prenom)s %(nom)s » a été créé."


class EnseignantUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Enseignant
    form_class = EnseignantForm
    template_name = "app/enseignant_form.html"
    success_url = reverse_lazy("enseignant_list")
    success_message = "L'enseignant « %(prenom)s %(nom)s » a été modifié."


class EnseignantDeleteView(LoginRequiredMixin, DeleteMessageMixin, DeleteView):
    model = Enseignant
    template_name = "app/enseignant_confirm_delete.html"
    success_url = reverse_lazy("enseignant_list")
    success_message = "L'enseignant « %(object)s » a été supprimé."


class EnseignantDetailView(DetailView):
    """Fiche détaillée d'un enseignant : cours, promotions et examens."""

    model = Enseignant
    template_name = "app/enseignant_detail.html"
    context_object_name = "enseignant"

    def get_queryset(self):
        return super().get_queryset().prefetch_related(
            Prefetch('cours', queryset=Cours.objects.select_related('promotion')))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        enseignant = self.object
        cours = (enseignant.cours
                 .select_related('promotion')
                 .order_by('promotion__nom', 'nom'))
        promotions = (Promotion.objects
                      .filter(cours__enseignant=enseignant)
                      .distinct()
                      .order_by('nom'))
        examens = (Examen.objects
                   .filter(cours__enseignant=enseignant)
                   .select_related('cours__promotion', 'session')
                   .annotate(nb_inscriptions=Count('inscriptions', distinct=True))
                   .order_by('date_examen'))
        nb_etudiants = (Etudiant.objects
                        .filter(promotion__cours__enseignant=enseignant)
                        .distinct()
                        .count())
        ctx.update({
            'cours': cours,
            'promotions': promotions,
            'examens': examens,
            'nb_etudiants': nb_etudiants,
        })
        return ctx


# --- CRUD SESSION ---
class SessionListView(SafePaginationMixin, ListView):
    model = Session
    template_name = "app/session_list.html"
    context_object_name = "sessions"
    paginate_by = 25


def _donnees_participants_session(session):
    """Données partagées par la vue détail et l'export PDF des participants.

    Retourne (examens, participants_par_promotion, nb_inscriptions,
    nb_participants) : les participants sont les étudiants uniques inscrits
    à au moins un examen de la session, regroupés par promotion.
    """
    examens = (session.examen_set
               .select_related('cours__promotion', 'cours__enseignant')
               .prefetch_related(Prefetch(
                   'inscriptions',
                   queryset=Inscription.objects
                   .select_related('etudiant')
                   .order_by('etudiant__nom', 'etudiant__prenom')))
               .order_by('date_examen', 'cours__nom'))
    participants_ids = set()
    nb_inscriptions = 0
    for examen in examens:
        inscriptions = examen.inscriptions.all()  # préchargé (Prefetch)
        nb_inscriptions += len(inscriptions)
        participants_ids.update(ins.etudiant_id for ins in inscriptions)

    participants = (Etudiant.objects
                    .filter(pk__in=participants_ids)
                    .select_related('promotion')
                    .annotate(nb_examens=Count(
                        'inscription',
                        filter=Q(inscription__examen__session=session)))
                    .order_by('promotion__nom', 'nom', 'prenom'))
    groupes = OrderedDict()
    for etu in participants:
        groupes.setdefault(etu.promotion, []).append(etu)
    return examens, list(groupes.items()), nb_inscriptions, len(participants_ids)


class SessionDetailView(DetailView):
    """Détail d'une session : examens programmés et participants.

    Les participants d'un examen sont ses inscriptions (modèle Inscription).
    L'onglet « Participants » regroupe les étudiants uniques de la session par
    promotion ; l'onglet « Cours » liste les examens programmés.
    """

    model = Session
    template_name = "app/session_detail.html"
    context_object_name = "session"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        session = self.object
        examens, groupes, nb_inscriptions, nb_participants = \
            _donnees_participants_session(session)
        participants = [etu for _, etus in groupes for etu in etus]

        ctx.update({
            'examens': examens,
            'nb_inscriptions': nb_inscriptions,
            'nb_participants': nb_participants,
            'participants': participants,
            'participants_par_promotion': groupes,
        })
        return ctx


@login_required
def session_participants_pdf(request, pk):
    """Exporte en PDF la liste des participants d'une session.

    Une section par promotion (étudiants uniques de la session), avec
    n°, n° étudiant, nom et nombre d'examens suivis — même contenu que
    l'onglet « Participants » du détail de la session.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    session = get_object_or_404(Session, pk=pk)
    _, groupes, _, nb_participants = _donnees_participants_session(session)

    tampon = BytesIO()
    doc = SimpleDocTemplate(
        tampon, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm,
        topMargin=15 * mm, bottomMargin=15 * mm,
        title=f"Participants - {session.nom}")
    styles = getSampleStyleSheet()
    elements = [
        Paragraph(f"Liste des participants — {session.nom}",
                  styles['Title']),
        Paragraph(
            f"{session.get_semestre_display()} · "
            f"{session.get_type_session_display()} · "
            f"du {session.date_debut:%d/%m/%Y} au {session.date_fin:%d/%m/%Y} · "
            f"{nb_participants} participant(s)",
            styles['Normal']),
        Spacer(1, 6 * mm),
    ]
    if not groupes:
        elements.append(Paragraph(
            "Aucun participant inscrit pour cette session.", styles['Normal']))
    for promotion, etudiants in groupes:
        elements.append(Paragraph(
            f"{promotion.nom} — {len(etudiants)} participant(s)",
            styles['Heading2']))
        lignes = [['N°', 'Nom et prénoms', 'Examens']]
        for i, etu in enumerate(etudiants, start=1):
            lignes.append([
                str(i), f"{etu.nom} {etu.prenom}", str(etu.nb_examens)])
        tableau = Table(lignes, colWidths=[12 * mm, 132 * mm, 20 * mm])
        tableau.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#212529')),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
            ('FONTSIZE', (0, 0), (-1, -1), 9),
            ('ALIGN', (0, 0), (0, -1), 'RIGHT'),
            ('ALIGN', (-1, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 0.4, colors.grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1),
             [colors.white, colors.HexColor('#f2f2f2')]),
        ]))
        elements.append(tableau)
        elements.append(Spacer(1, 5 * mm))
    doc.build(elements)
    tampon.seek(0)
    return FileResponse(
        tampon, as_attachment=True, content_type='application/pdf',
        filename=f"participants-{slugify(session.nom)}.pdf")


class SessionCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Session
    form_class = SessionForm
    template_name = "app/session_form.html"
    success_url = reverse_lazy("session_list")
    success_message = "La session « %(nom)s » a été créée."


class SessionUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Session
    form_class = SessionForm
    template_name = "app/session_form.html"
    success_url = reverse_lazy("session_list")
    success_message = "La session « %(nom)s » a été modifiée."


class SessionDeleteView(LoginRequiredMixin, DeleteMessageMixin, DeleteView):
    model = Session
    template_name = "app/session_confirm_delete.html"
    success_url = reverse_lazy("session_list")
    success_message = "La session « %(object)s » a été supprimée."


# --- CRUD COURS ---
class CoursListView(SafePaginationMixin, ListView):
    """Liste des cours, filtrable par recherche, promotion et enseignant."""
    model = Cours
    template_name = "app/cours_list.html"
    context_object_name = "cours_list"
    paginate_by = 25

    def get_queryset(self):
        qs = Cours.objects.select_related('enseignant', 'promotion')
        params = self.request.GET
        self.filters = {}

        q = params.get('q', '').strip()
        if q:
            qs = qs.filter(nom__icontains=q)
            self.filters['q'] = q

        promo = params.get('promotion', '').strip()
        if promo.isdigit():
            qs = qs.filter(promotion__pk=promo)
            self.filters['promotion'] = int(promo)

        ens = params.get('enseignant', '').strip()
        if ens.isdigit():
            qs = qs.filter(enseignant__pk=ens)
            self.filters['enseignant'] = int(ens)

        return qs.order_by('promotion__nom', 'nom')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Options des filtres : uniquement les valeurs réellement utilisées
        context['filter_promotions'] = (Promotion.objects
                                        .filter(cours__isnull=False)
                                        .distinct().order_by('nom'))
        context['filter_enseignants'] = (Enseignant.objects
                                         .filter(cours__isnull=False)
                                         .distinct().order_by('nom', 'prenom'))
        context['filters'] = self.filters
        context['total_cours'] = Cours.objects.count()
        return context


class CoursCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    model = Cours
    form_class = CoursForm
    template_name = "app/cours_form.html"
    success_url = reverse_lazy("cours_list")
    success_message = "Le cours « %(nom)s » a été créé."


class CoursUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Cours
    form_class = CoursForm
    template_name = "app/cours_form.html"
    success_url = reverse_lazy("cours_list")
    success_message = "Le cours « %(nom)s » a été modifié."


class CoursDeleteView(LoginRequiredMixin, DeleteMessageMixin, DeleteView):
    model = Cours
    template_name = "app/cours_confirm_delete.html"
    success_url = reverse_lazy("cours_list")
    success_message = "Le cours « %(object)s » a été supprimé."


# --- CRUD EXAMEN ---
class ExamenListView(SafePaginationMixin, ListView):
    """Liste des examens, filtrable par recherche, promotion, enseignant et session."""
    model = Examen
    template_name = "app/examen_list.html"
    context_object_name = "examens"
    paginate_by = 25

    def get_queryset(self):
        qs = (Examen.objects
              .select_related('cours__promotion', 'cours__enseignant', 'session')
              .annotate(nb_inscriptions=Count('inscriptions', distinct=True)))
        params = self.request.GET
        self.filters = {}
        self.tri = params.get('tri', 'cours').strip() or 'cours'

        q = params.get('q', '').strip()
        if q:
            qs = qs.filter(Q(cours__nom__icontains=q)
                           | Q(cours__promotion__nom__icontains=q)
                           | Q(cours__enseignant__nom__icontains=q)
                           | Q(cours__enseignant__prenom__icontains=q)
                           | Q(session__nom__icontains=q))
            self.filters['q'] = q

        promo = params.get('promotion', '').strip()
        if promo.isdigit():
            qs = qs.filter(cours__promotion__pk=promo)
            self.filters['promotion'] = int(promo)

        ens = params.get('enseignant', '').strip()
        if ens.isdigit():
            qs = qs.filter(cours__enseignant__pk=ens)
            self.filters['enseignant'] = int(ens)

        session = params.get('session', '').strip()
        if session.isdigit():
            qs = qs.filter(session__pk=session)
            self.filters['session'] = int(session)

        tris_autorises = {
            'cours': ('cours__nom', 'date_examen'),
            '-cours': ('-cours__nom', '-date_examen'),
            'promotion': ('cours__promotion__nom', 'cours__nom', 'date_examen'),
            '-promotion': ('-cours__promotion__nom', '-cours__nom', '-date_examen'),
            'session': ('session__nom', 'cours__nom', 'date_examen'),
            '-session': ('-session__nom', '-cours__nom', '-date_examen'),
            'date': ('date_examen', 'cours__nom'),
            '-date': ('-date_examen', '-cours__nom'),
        }
        if self.tri not in tris_autorises:
            self.tri = 'cours'
        self.filters['tri'] = self.tri
        return qs.order_by(*tris_autorises[self.tri])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Options des filtres : uniquement les valeurs réellement utilisées
        context['filter_promotions'] = (Promotion.objects
                                        .filter(cours__examen__isnull=False)
                                        .distinct().order_by('nom'))
        context['filter_enseignants'] = (Enseignant.objects
                                         .filter(cours__examen__isnull=False)
                                         .distinct().order_by('nom', 'prenom'))
        context['filter_sessions'] = (Session.objects
                                      .filter(examen__isnull=False)
                                      .distinct().order_by('date_debut', 'nom'))
        context['filters'] = self.filters
        # Chaîne de requête sans « tri » ni « page », pour les liens de tri
        # qui conservent les filtres courants.
        params = self.request.GET.copy()
        params.pop('tri', None)
        params.pop('page', None)
        context['base_qs'] = params.urlencode()
        context['tri'] = self.tri
        context['total_examens'] = Examen.objects.count()
        return context


class ExamenCreateView(LoginRequiredMixin, SuccessMessageMixin, CreateView):
    """Aucune inscription pré-créée : la fiche de cote se construit
    dynamiquement à partir des étudiants de la promotion du cours.
    Une ligne d'inscription n'est créée en base que lorsqu'une note est saisie.
    """
    model = Examen
    form_class = ExamenForm
    template_name = "app/examen_form.html"
    success_url = reverse_lazy("examen_list")
    success_message = "L'examen a été créé."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['cours_data'] = list(Cours.objects
                                 .select_related('promotion', 'enseignant')
                                 .order_by('promotion__nom', 'nom')
                                 .values_list('id', 'nom', 'promotion__nom',
                                              'enseignant__nom', 'enseignant__prenom'))
        return ctx


class ExamenUpdateView(LoginRequiredMixin, SuccessMessageMixin, UpdateView):
    model = Examen
    form_class = ExamenForm
    template_name = "app/examen_form.html"
    success_url = reverse_lazy("examen_list")
    success_message = "L'examen a été modifié."

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx['cours_data'] = list(Cours.objects
                                 .select_related('promotion', 'enseignant')
                                 .order_by('promotion__nom', 'nom')
                                 .values_list('id', 'nom', 'promotion__nom',
                                              'enseignant__nom', 'enseignant__prenom'))
        examen = self.object
        ctx['inscriptions'] = (examen.inscriptions
                               .select_related('etudiant')
                               .order_by('etudiant__nom', 'etudiant__prenom'))
        ctx['inscriptibles'] = (Etudiant.objects
                                .filter(promotion=examen.cours.promotion)
                                .exclude(pk__in=examen.inscriptions
                                         .values_list('etudiant_id', flat=True))
                                .order_by('nom', 'prenom'))
        return ctx


class ExamenDeleteView(LoginRequiredMixin, DeleteMessageMixin, DeleteView):
    model = Examen
    template_name = "app/examen_confirm_delete.html"
    success_url = reverse_lazy("examen_list")
    success_message = "L'examen « %(object)s » a été supprimé."


@login_required
def fiche_cote(request, pk):
    """Fiche de cote d'un examen (modèle du PDF « Fiche_ETHIQUE & DEONTOLOGIE »).

    Vue en lecture seule, destinée à être imprimée : les notes sont saisies
    manuscritement sur la fiche papier. Les étudiants sont inscrits au fur
    et à mesure (bouton « Ajouter un étudiant »), la fiche n'affiche que les
    étudiants effectivement inscrits.
    """
    examen = get_object_or_404(
        Examen.objects.select_related('cours__promotion', 'cours__enseignant', 'session'),
        pk=pk)
    inscriptions = (examen.inscriptions
                    .select_related('etudiant')
                    .order_by('etudiant__nom', 'etudiant__prenom'))

    inscrits_ids = inscriptions.values_list('etudiant_id', flat=True)
    inscriptibles = (Etudiant.objects
                     .filter(promotion=examen.cours.promotion)
                     .exclude(pk__in=inscrits_ids)
                     .order_by('nom', 'prenom'))
    return render(request, 'app/fiche_cote.html', {
        'examen': examen,
        'inscriptions': inscriptions,
        'inscriptibles': inscriptibles,
        'titulaire': examen.cours.enseignant,
    })


@login_required
def examen_inscrire(request, pk):
    """Inscrit un ou plusieurs étudiants (de la promotion du cours) à un examen.

    Les étudiants sont choisis via des cases à cocher (champ POST
    « etudiants », multiples) ; le champ « etudiant » unique reste accepté.
    « next » permet de revenir à la page d'édition plutôt qu'à la fiche.
    """
    examen = get_object_or_404(Examen.objects.select_related('cours'), pk=pk)
    if request.method == 'POST':
        pks = [v for v in request.POST.getlist('etudiants')
               if v.isdigit()]
        if not pks and request.POST.get('etudiant', '').isdigit():
            pks = [request.POST['etudiant']]
        if not pks:
            messages.warning(request, "Aucun étudiant sélectionné.")
            return redirect(_retour_examen(request, examen))
        existants = set(examen.inscriptions.values_list('etudiant_id', flat=True))
        candidats = (Etudiant.objects
                     .filter(pk__in=[int(v) for v in pks],
                             promotion=examen.cours.promotion)
                     .exclude(pk__in=existants))
        crees = Inscription.objects.bulk_create(
            [Inscription(examen=examen, etudiant=e) for e in candidats])
        deja = len(pks) - len(candidats)
        if crees:
            messages.success(
                request,
                f"{len(crees)} étudiant(s) ajouté(s) à la fiche de cote.")
        if deja > 0:
            messages.info(request, f"{deja} étudiant(s) figuraient déjà sur la fiche.")
        if not crees and deja == 0:
            messages.warning(request, "Aucun étudiant ajouté.")
    return redirect(_retour_examen(request, examen))


@login_required
def examen_inscrire_tous(request, pk):
    """Inscrit d'un coup tous les étudiants de la promotion du cours
    qui ne sont pas encore sur la fiche de cote."""
    examen = get_object_or_404(Examen.objects.select_related('cours__promotion'), pk=pk)
    if request.method == 'POST':
        existants = examen.inscriptions.values_list('etudiant_id', flat=True)
        manquants = (Etudiant.objects
                     .filter(promotion=examen.cours.promotion)
                     .exclude(pk__in=existants))
        crees = Inscription.objects.bulk_create(
            [Inscription(examen=examen, etudiant=e) for e in manquants])
        if crees:
            messages.success(
                request,
                f"{len(crees)} étudiant(s) de la promotion "
                f"« {examen.cours.promotion.nom} » ajouté(s) à la fiche.")
        else:
            messages.info(request, "Tous les étudiants de la promotion figurent déjà sur la fiche.")
    return redirect(_retour_examen(request, examen))


@login_required
def examen_desinscrire(request, pk, inscription_pk):
    """Retire un étudiant d'une fiche de cote (supprime aussi ses notes)."""
    examen = get_object_or_404(Examen, pk=pk)
    if request.method == 'POST':
        inscription = get_object_or_404(Inscription, pk=inscription_pk,
                                        examen=examen)
        nom = str(inscription.etudiant)
        inscription.delete()
        messages.success(request, f"{nom} a été retiré de la fiche de cote.")
    return redirect(_retour_examen(request, examen))


def _retour_examen(request, examen):
    """Page de retour après inscription/désinscription : fiche par défaut,
    page d'édition si « next » pointe dessus (évite toute redirection ouverte)."""
    nxt = request.POST.get('next', '')
    if nxt.startswith(f'/examens/{examen.pk}/edit/'):
        return nxt
    return f'/examens/{examen.pk}/fiche/'
